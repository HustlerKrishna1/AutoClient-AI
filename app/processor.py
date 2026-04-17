"""
LeadProcessor — Ollama AI enrichment for AutoClient AI v2.

Improvements over v1:
  - Configurable sender name/company via env vars (SENDER_NAME, SENDER_COMPANY)
  - Richer scoring prompt with 2 few-shot examples + niche-aware criteria
  - Generates 2 subject-line variants per lead (separated by ---VARIANT---)
  - num_ctx increased 2048 → 4096 for richer context window
  - Parallel Ollama calls via asyncio.Semaphore (max 2 concurrent)
  - In-process lead cache to skip re-processing identical leads
  - More robust JSON parsing (specific key regex over greedy {.*?})
  - Structured output: subject_line extracted separately from draft body
"""

import asyncio
import hashlib
import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL   = "http://localhost:11434/api/generate"
MODEL_NAME   = os.environ.get("OLLAMA_MODEL", "llama3:8b")
SENDER_NAME  = os.environ.get("SENDER_NAME",  "Alex")
SENDER_COMPANY = os.environ.get("SENDER_COMPANY", "GrowthForge")

# Max 2 simultaneous Ollama requests — keeps RAM inside 8 GB headroom
_OLLAMA_SEMAPHORE = asyncio.Semaphore(2)

# Module-level cache: hash(name+website) → {score, draft, subject_line}
_LEAD_CACHE: dict[str, dict] = {}

# ── Niche-specific scoring hints ─────────────────────────────────────────────
_NICHE_HINTS: dict[str, str] = {
    "plumber":       "Prioritise businesses without visible online booking or modern websites.",
    "photographer":  "Prioritise photographers without an online gallery or booking form.",
    "accountant":    "Prioritise small firms without a client portal or digital-first branding.",
    "lawyer":        "Prioritise solo practitioners without a professional web presence.",
    "restaurant":    "Prioritise restaurants without an online ordering or reservation system.",
    "dentist":       "Prioritise practices without a patient portal or appointment scheduler.",
    "gym":           "Prioritise gyms without a membership management or class-booking platform.",
    "realestate":    "Prioritise agents relying on generic portal listings rather than personal sites.",
    "electrician":   "Prioritise tradespeople without a review strategy or digital booking.",
    "cleaner":       "Prioritise cleaning companies without automated quote or recurring booking.",
}

def _niche_hint(niche: str) -> str:
    niche_lower = niche.lower()
    for key, hint in _NICHE_HINTS.items():
        if key in niche_lower:
            return hint
    return "Prioritise businesses that appear to lack a strong digital or marketing presence."


# ── Prompt templates ──────────────────────────────────────────────────────────

_SCORE_PROMPT = """\
You are a B2B sales analyst scoring business leads.

SCORING EXAMPLES:
- Score 9: "Family Plumbing Co, website has no booking form, only a phone number listed, \
clearly old site last updated 2019" → They urgently need digital marketing help.
- Score 3: "AcmePlumbing Enterprise, fully modern site with online booking, live chat, \
Google reviews widget, and SEO blog" → They're already well-served digitally.

NICHE CONTEXT: {niche_hint}

NOW SCORE THIS LEAD:
Business name : {name}
Website       : {website}
Description   : {snippet}
Niche/service : {niche}
Location      : {location}

Reply ONLY with a valid JSON object, no other text:
{{"score": <integer 1-10>, "reason": "<one sentence>"}}"""

_EMAIL_PROMPT = """\
You are an expert cold-email copywriter.

Write TWO versions of a cold outreach email for the business below.
They should differ only in the Subject line — same body.

Business name : {name}
Website       : {website}
Score         : {score}/10
Context       : {snippet}

FORMAT (output exactly this structure, nothing else):
SUBJECT_A: <subject line variant A — curiosity-driven>
SUBJECT_B: <subject line variant B — benefit-driven>
BODY:
<email body, max 130 words, sound human, reference their specific business,\
 end with low-pressure CTA, sign off as "{sender_name} from {sender_company}">"""


class LeadProcessor:
    """Async processor. Create once, call process() per lead, then close()."""

    def __init__(self) -> None:
        # 10-min timeout — llama3:8b on CPU can take 5-7 min per call
        self._client = httpx.AsyncClient(timeout=600.0)

    # ── Public API ────────────────────────────────────────────────────────────

    async def process(
        self,
        lead: dict,
        niche: str = "",
        location: str = "",
    ) -> dict:
        """
        Returns: {score, draft, subject_line, subject_line_b, status}
        status: "processed" | "model_offline"
        """
        name    = lead.get("name", "Unknown Business")
        website = lead.get("website", "")
        snippet = (lead.get("snippet") or "No description available.")[:500]

        # Cache check — skip if we've seen this exact lead before
        cache_key = hashlib.md5(f"{name}|{website}".encode()).hexdigest()
        if cache_key in _LEAD_CACHE:
            logger.info(f"[Processor] Cache hit for {name[:40]!r}")
            return _LEAD_CACHE[cache_key]

        # ── Score ─────────────────────────────────────────────────────────────
        async with _OLLAMA_SEMAPHORE:
            score_raw = await self._call_ollama(
                _SCORE_PROMPT.format(
                    niche_hint=_niche_hint(niche),
                    name=name,
                    website=website,
                    snippet=snippet,
                    niche=niche or "general",
                    location=location or "unspecified",
                )
            )

        if score_raw is None:
            return self._offline_result()

        score, reason = self._parse_score(score_raw)

        # ── Email draft ───────────────────────────────────────────────────────
        async with _OLLAMA_SEMAPHORE:
            draft_raw = await self._call_ollama(
                _EMAIL_PROMPT.format(
                    name=name,
                    website=website,
                    score=score,
                    snippet=snippet,
                    sender_name=SENDER_NAME,
                    sender_company=SENDER_COMPANY,
                )
            )

        if draft_raw is None:
            return self._offline_result(score=score)

        subject_a, subject_b, body = self._parse_email(draft_raw)

        # Full draft stored as: Subject A header + body, with variant B appended
        full_draft = f"Subject: {subject_a}\n\n{body}"
        if subject_b:
            full_draft += f"\n\n---VARIANT B---\nSubject: {subject_b}\n\n{body}"

        result = {
            "score":          score,
            "draft":          full_draft.strip(),
            "subject_line":   subject_a,
            "subject_line_b": subject_b,
            "status":         "processed",
        }
        _LEAD_CACHE[cache_key] = result
        return result

    async def close(self) -> None:
        await self._client.aclose()

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _call_ollama(self, prompt: str) -> str | None:
        payload = {
            "model":  MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 400,   # reduced: faster + fits 8 GB RAM
                "num_ctx":     2048,  # reduced from 4096 for RAM headroom
            },
        }
        try:
            resp = await self._client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except httpx.ConnectError:
            logger.warning("[Processor] Ollama offline — ConnectError localhost:11434")
            return None
        except httpx.TimeoutException as exc:
            logger.warning(f"[Processor] Ollama timeout ({type(exc).__name__}) — marking model_offline")
            return None
        except httpx.HTTPStatusError as exc:
            logger.error(f"[Processor] Ollama HTTP {exc.response.status_code}")
            return None
        except Exception as exc:
            logger.error(f"[Processor] Unexpected: {exc}", exc_info=True)
            return None

    @staticmethod
    def _parse_score(text: str) -> tuple[float, str]:
        # Targeted key extraction — more reliable than greedy {.*?}
        score_match  = re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', text)
        reason_match = re.search(r'"reason"\s*:\s*"([^"]*)"', text)

        if score_match:
            score = float(score_match.group(1))
            reason = reason_match.group(1) if reason_match else ""
            return round(min(max(score, 1.0), 10.0), 1), reason

        # Fallback: first standalone number 1-10
        nums = re.findall(r'\b(10|[1-9])\b', text)
        if nums:
            return float(nums[0]), ""

        return 5.0, "Parse failed"

    @staticmethod
    def _parse_email(text: str) -> tuple[str, str, str]:
        """
        Parse the structured email output.
        Returns (subject_a, subject_b, body).
        """
        subject_a = ""
        subject_b = ""
        body      = ""

        sa_match = re.search(r"SUBJECT_A\s*:\s*(.+)", text, re.IGNORECASE)
        sb_match = re.search(r"SUBJECT_B\s*:\s*(.+)", text, re.IGNORECASE)
        body_match = re.search(r"BODY\s*:\s*\n([\s\S]+)", text, re.IGNORECASE)

        if sa_match:
            subject_a = sa_match.group(1).strip().strip("*").strip()
        if sb_match:
            subject_b = sb_match.group(1).strip().strip("*").strip()
        if body_match:
            body = body_match.group(1).strip()
        else:
            # Fallback: everything after the last SUBJECT_ line
            body = text.strip()

        # Last resort: pull Subject: from anywhere
        if not subject_a:
            s_match = re.search(r"Subject\s*:\s*(.+)", text, re.IGNORECASE)
            if s_match:
                subject_a = s_match.group(1).strip()
            else:
                subject_a = "Quick question"

        return subject_a, subject_b, body

    @staticmethod
    def _offline_result(score: float = 0.0) -> dict:
        return {
            "score":          score,
            "draft":          (
                "⚠️  Model Offline — Ollama is not running at localhost:11434.\n"
                "Start it with: ollama serve"
            ),
            "subject_line":   "",
            "subject_line_b": "",
            "status":         "model_offline",
        }

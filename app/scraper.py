"""
AutoClient AI — Lead scraper v3.1

Approach (tested & working):
  Phase 1a — Navigate to Google Maps search → extract ALL business cards
             in one JavaScript call (name, phone, place URL). No clicking.
  Phase 1b — For each business, navigate directly to its Google Maps place
             URL → extract the real website from a[data-item-id='authority'].
  Phase 2  — Visit each business website to hunt for an email address.

Why this works vs previous attempts:
  - DDG / Bing httpx: HTTP 202 (bot-block).
  - DDG / Bing Playwright browser: no <a href> links in results DOM.
  - Google search: bot-detection page.
  - Google Maps click + go_back: Maps SPA loses state between clicks.
  - Google Maps direct place URL: works reliably once we have the data= URL.
"""

import asyncio
import logging
import random
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,10}\b"
)

_PHONE_RE = re.compile(
    r"(?:\+?1[\s\-.]?)?"           # optional US country code
    r"(?:\(?\d{3}\)?[\s\-.]?)"     # area code
    r"\d{3}[\s\-.]?\d{4}"          # local number
)

_EMAIL_JUNK = (
    ".png", ".jpg", ".gif", ".svg", ".css", ".js", ".woff",
    "example.com", "yourdomain", "domain.com", "email.com",
    "sentry.io", "wixpress.com", "schema.org", "noreply",
    "no-reply", "donotreply", "test@", "user@", "name@",
)

_CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/get-in-touch",
    "/reach-us",
]

_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0",
]

# JS phone regex (JS does not support Python's verbose syntax)
_JS_PHONE_RE = r"(?:\+?1[\s\-.]?)?(?:\(?\d{3}\)?[\s\-.]?)\d{3}[\s\-\.]\d{4}"

# Google Maps JS snippet: extract all business cards in one shot
_MAPS_EXTRACT_JS = f"""
() => {{
    const PHONE = /{_JS_PHONE_RE}/;
    const SKIP  = /^(sponsored|ad|advertisement)$/i;
    const items = [];
    document.querySelectorAll("div[role=article]").forEach(card => {{
        const nameEl = card.querySelector(".fontHeadlineSmall");
        const linkEl = card.querySelector("a");
        if (!nameEl || !linkEl) return;
        const name = nameEl.innerText.trim();
        if (!name || SKIP.test(name)) return;
        const phoneMatch = card.innerText.match(PHONE);
        items.push({{
            name:  name,
            href:  linkEl.href,
            phone: phoneMatch ? phoneMatch[0] : null,
        }});
    }});
    return items;
}}
"""


def _get_random_ua() -> str:
    try:
        from fake_useragent import UserAgent
        return UserAgent().chrome
    except Exception:
        return random.choice(_FALLBACK_UAS)


def _extract_emails(soup: BeautifulSoup, raw_html: str) -> list[str]:
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if addr:
                found.append(addr)
    found += _EMAIL_RE.findall(raw_html)
    clean = [e for e in found if not any(j in e.lower() for j in _EMAIL_JUNK)]
    seen: set[str] = set()
    result: list[str] = []
    for e in clean:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


def _clean_website(url: str | None) -> str | None:
    """Strip Google tracking params, return base URL."""
    if not url:
        return None
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/") or None
    except Exception:
        return url


class LeadScraper:
    __slots__ = ()

    async def scrape(
        self,
        niche: str,
        location: str,
        max_results: int = 10,
    ) -> list[dict]:
        """
        Single Playwright browser session:
          1. Google Maps search → bulk-extract name/phone/place-URL from cards.
          2. Per business: navigate to Maps place URL → extract real website.
          3. Per website: visit pages to find email.
        """
        from urllib.parse import quote_plus

        query   = f"{niche} {location}"
        maps_url = f"https://www.google.com/maps/search/{quote_plus(query)}/"
        raw_leads: list[dict] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--blink-settings=imagesEnabled=false",
                ],
            )
            try:
                ctx = await browser.new_context(
                    user_agent=_get_random_ua(),
                    viewport={"width": 1280, "height": 900},
                    java_script_enabled=True,
                    ignore_https_errors=True,
                )
                page = await ctx.new_page()

                # ── Phase 1a: bulk extract card data ─────────────────────────
                raw_leads = await self._maps_search(page, maps_url, max_results)
                logger.info(f"[Scraper] Maps search returned {len(raw_leads)} businesses")

                if not raw_leads:
                    logger.warning("[Scraper] No businesses found on Google Maps.")
                    return []

                # ── Phase 1b: get real website per business ───────────────────
                await self._maps_get_websites(page, raw_leads)

                # ── Phase 2: email enrichment ────────────────────────────────
                await self._enrich_emails(page, raw_leads)

            except Exception as exc:
                logger.error(f"[Scraper] Fatal error: {exc}", exc_info=True)
            finally:
                await browser.close()
                logger.info("[Scraper] Browser closed.")

        return [l for l in raw_leads if l.get("name")]

    # ── Phase 1a: Google Maps search ──────────────────────────────────────────

    async def _maps_search(
        self, page, maps_url: str, max_results: int
    ) -> list[dict]:
        logger.info(f"[Scraper] Maps: {maps_url}")
        try:
            await page.goto(maps_url, wait_until="commit", timeout=30_000)
        except Exception as exc:
            logger.warning(f"[Scraper] Maps navigation error: {exc}")
            return []

        # Wait for at least one business card to appear
        try:
            await page.wait_for_selector("div[role=article]", timeout=30_000)
            await page.wait_for_timeout(2000)  # let remaining cards render
        except PlaywrightTimeout:
            logger.warning("[Scraper] Maps: no business cards appeared")
            return []

        cards: list[dict] = await page.evaluate(_MAPS_EXTRACT_JS)
        logger.info(f"[Scraper] Cards extracted: {len(cards)}")

        seen: set[str] = set()
        leads: list[dict] = []
        for c in cards:
            name_key = c["name"].lower()
            if name_key in seen:
                continue
            seen.add(name_key)
            leads.append({
                "name":         c["name"],
                "_maps_href":   c["href"],   # used in Phase 1b, removed after
                "phone":        c["phone"],
                "website":      None,
                "email":        None,
                "email_source": None,
                "snippet":      "",
            })
            if len(leads) >= max_results:
                break

        return leads

    # ── Phase 1b: get real website for each business ──────────────────────────

    async def _maps_get_websites(self, page, leads: list[dict]) -> None:
        """
        Navigate to each business's Google Maps place URL and extract
        the real website from a[data-item-id='authority'].
        """
        logger.info(f"[Scraper] _maps_get_websites: processing {len(leads)} leads")
        for idx, lead in enumerate(leads):
            href = lead.pop("_maps_href", None)
            logger.info(f"[Scraper] [{idx+1}/{len(leads)}] {lead['name'][:40]!r} href={str(href)[:80]!r}")

            if not href:
                logger.info(f"[Scraper] [{idx+1}] skipping - no href")
                continue
            if "google.com/maps" not in href:
                logger.info(f"[Scraper] [{idx+1}] skipping - not a Maps URL: {href[:80]}")
                continue

            try:
                await page.goto(href, wait_until="commit", timeout=20_000)
                await page.wait_for_selector(
                    "a[data-item-id='authority']", timeout=8_000
                )
                raw_url = await page.evaluate(
                    """() => {
                        const el = document.querySelector("a[data-item-id='authority']");
                        return el ? el.href : null;
                    }"""
                )
                lead["website"] = _clean_website(raw_url)
                logger.info(f"[Scraper] [{idx+1}] website={lead['website']}")
            except PlaywrightTimeout:
                logger.info(f"[Scraper] [{idx+1}] no website found on Maps (timeout)")
            except Exception as exc:
                logger.warning(f"[Scraper] [{idx+1}] website fetch error for {lead['name']!r}: {exc}")

            logger.info(
                f"[Scraper] [{idx+1}/{len(leads)}] {lead['name'][:40]!r}"
                f"  phone={lead['phone'] or '-'}"
                f"  site={lead['website'] or '-'}"
            )
            if idx < len(leads) - 1:
                await asyncio.sleep(random.uniform(0.3, 0.8))

    # ── Phase 2: email enrichment ─────────────────────────────────────────────

    async def _enrich_emails(self, page, leads: list[dict]) -> None:
        """Visit each lead's website to find an email address."""
        needs_email = [l for l in leads if l.get("website") and not l.get("email")]

        for idx, lead in enumerate(needs_email):
            try:
                email, source = await self._hunt_email(page, lead["website"])
            except Exception as exc:
                logger.debug(f"[Scraper] Email hunt error for {lead['name']!r}: {exc}")
                email, source = None, None

            if email:
                lead["email"]        = email
                lead["email_source"] = source

            logger.info(
                f"[Scraper] Email [{idx+1}/{len(needs_email)}] "
                f"{lead['name'][:35]!r}  "
                f"email={lead.get('email') or '-'}"
            )
            if idx < len(needs_email) - 1:
                await asyncio.sleep(random.uniform(0.5, 1.5))

    async def _hunt_email(
        self, page, base_url: str
    ) -> tuple[str | None, str | None]:
        """Check homepage + contact paths for an email address."""
        try:
            parsed = urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                return None, None
        except Exception:
            return None, None

        urls_to_try = [base_url] + [
            base_url.rstrip("/") + path for path in _CONTACT_PATHS
        ]

        for target in urls_to_try:
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=12_000)
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                emails = _extract_emails(soup, html)
                if emails:
                    return emails[0], target
            except PlaywrightTimeout:
                logger.debug(f"[Scraper] Timeout: {target}")
            except Exception as exc:
                logger.debug(f"[Scraper] Could not visit {target}: {exc}")

        return None, None

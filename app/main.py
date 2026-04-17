"""
AutoClient AI — FastAPI backend v2 (complete, working build).

Routes implemented:
  GET  /                   → serve index.html dashboard
  GET  /health             → server + Ollama status
  POST /scour              → start scrape + AI pipeline (background)
  GET  /status             → live pipeline state
  GET  /leads              → filtered / paginated leads
  GET  /leads/stats        → aggregate KPIs
  GET  /leads/export       → CSV download
  PUT  /leads/{id}         → patch notes / draft / dnc / email / phone
  DELETE /leads            → bulk delete
  POST /send/{id}          → simulate email send
  GET  /config             → server config (model, sender)
  GET  /jobs               → job history (last 50)
  POST /campaigns          → create campaign
  GET  /campaigns          → list campaigns
  POST /campaigns/{id}/send-all  → send all ready leads in campaign
  DELETE /campaigns/{id}   → archive campaign
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.database import (
    archive_campaign,
    bulk_delete,
    create_campaign,
    export_leads_csv,
    get_campaigns,
    get_lead,
    get_leads_filtered,
    get_stats,
    init_db,
    update_lead_ai,
    update_lead_partial,
    update_lead_status,
    upsert_lead,
)
from app.processor import OLLAMA_URL, MODEL_NAME, SENDER_COMPANY, SENDER_NAME, LeadProcessor
from app.scraper import LeadScraper
from app.scheduler import (
    list_scheduled_jobs,
    remove_scheduled_job,
    schedule_campaign,
    start_scheduler,
    stop_scheduler,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
# Resolve to project root (one level up from app/)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX_HTML = os.path.join(_BASE_DIR, "index.html")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="AutoClient AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global state ──────────────────────────────────────────────────────────────
_pipeline_lock = asyncio.Lock()

_status: dict = {
    "running":    False,
    "message":    "Idle — ready.",
    "found":      0,
    "processed":  0,
    "errors":     0,
    "total":      0,
    "started_at": None,
}

_job_history: list[dict] = []


# ── Pydantic schemas ──────────────────────────────────────────────────────────
class ScourRequest(BaseModel):
    niche:       str
    location:    str
    max_results: int = 10
    campaign_id: Optional[int] = None


class LeadUpdateRequest(BaseModel):
    notes:          Optional[str]  = None
    edited_draft:   Optional[str]  = None
    do_not_contact: Optional[bool] = None
    email:          Optional[str]  = None
    phone:          Optional[str]  = None
    contact_name:   Optional[str]  = None


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class CampaignCreateRequest(BaseModel):
    name:     str
    niche:    str
    location: str


class ScheduleRequest(BaseModel):
    job_id:      str
    niche:       str
    location:    str
    max_results: int = 10
    campaign_id: Optional[int] = None
    cron_expr:   str  # e.g. "0 9 * * 1" (Monday 9 AM UTC)


# ── Pipeline ──────────────────────────────────────────────────────────────────
async def _run_pipeline(
    niche: str,
    location: str,
    max_results: int,
    campaign_id: Optional[int] = None,
) -> None:
    """
    Full scrape → save → AI-enrich pipeline.
    Runs as a FastAPI BackgroundTask; mutates _status in-place.
    """
    global _status

    # Double-check lock (BackgroundTasks may queue requests)
    if _pipeline_lock.locked():
        logger.warning("[Pipeline] Already running — skipped duplicate request.")
        return

    async with _pipeline_lock:
        _status.update({
            "running":    True,
            "message":    f"🔍 Scraping '{niche}' in {location}…",
            "found":      0,
            "processed":  0,
            "errors":     0,
            "total":      0,
            "started_at": datetime.utcnow().isoformat(),
        })

        job_record: dict = {
            "niche":       niche,
            "location":    location,
            "found":       0,
            "processed":   0,
            "errors":      0,
            "finished_at": None,
        }

        try:
            # ── Phase 1: Scrape ──────────────────────────────────────────────
            logger.info(f"[Pipeline] START  niche={niche!r}  location={location!r}  max={max_results}")
            scraper   = LeadScraper()
            raw_leads = await scraper.scrape(niche, location, max_results)

            found = len(raw_leads)
            _status["found"] = found
            _status["total"] = found
            _status["message"] = f"📋 Found {found} leads — saving + enriching…"
            job_record["found"] = found
            logger.info(f"[Pipeline] Scraped {found} leads")

            # ── Phase 2: Persist raw leads ───────────────────────────────────
            saved_items: list[tuple[int, dict]] = []
            for lead in raw_leads:
                data = {
                    "name":         lead.get("name", "Unknown"),
                    "website":      lead.get("website"),
                    "email":        lead.get("email"),
                    "phone":        lead.get("phone"),
                    "email_source": lead.get("email_source"),
                    "niche":        niche,
                    "location":     location,
                    "status":       "new",
                    "campaign_id":  campaign_id,
                }
                try:
                    lead_id, inserted = upsert_lead(data)
                    saved_items.append((lead_id, lead))
                    action = "inserted" if inserted else "exists"
                    logger.debug(f"[Pipeline] Lead {action}: {data['name'][:40]!r}")
                except Exception as exc:
                    logger.error(f"[Pipeline] DB save failed for {data.get('name')!r}: {exc}")
                    _status["errors"] += 1

            # ── Phase 3: AI enrichment ───────────────────────────────────────
            processor = LeadProcessor()
            try:
                for idx, (lead_id, lead) in enumerate(saved_items):
                    name = lead.get("name", "?")[:40]
                    _status["message"] = f"🤖 AI [{idx+1}/{len(saved_items)}]: {name}…"

                    # Mark as processing so the dashboard shows progress
                    update_lead_status(lead_id, "processing")

                    try:
                        result = await processor.process(lead, niche=niche, location=location)
                        update_lead_ai(
                            lead_id,
                            score=result["score"],
                            draft=result["draft"],
                            status=result["status"],
                            subject_line=result.get("subject_line", ""),
                            subject_line_b=result.get("subject_line_b", ""),
                        )
                        _status["processed"] += 1
                        logger.info(
                            f"[Pipeline] [{idx+1}/{len(saved_items)}] {name!r} "
                            f"score={result['score']}  status={result['status']}"
                        )
                    except Exception as exc:
                        logger.error(f"[Pipeline] AI error for lead_id={lead_id}: {exc}", exc_info=True)
                        update_lead_status(lead_id, "error")
                        _status["errors"] += 1
            finally:
                await processor.close()

            job_record["processed"] = _status["processed"]
            job_record["errors"]    = _status["errors"]

            summary = (
                f"✅ Done! {_status['processed']}/{found} enriched"
                + (f", {_status['errors']} errors" if _status["errors"] else "")
            )
            _status["message"] = summary
            logger.info(f"[Pipeline] COMPLETE — {summary}")

        except Exception as exc:
            logger.error(f"[Pipeline] FATAL: {exc}", exc_info=True)
            _status["message"] = f"❌ Pipeline error: {exc}"
            _status["errors"]  += 1
            job_record["errors"] += 1

        finally:
            _status["running"]       = False
            job_record["finished_at"] = datetime.utcnow().isoformat()
            _job_history.insert(0, job_record)
            # Keep only last 50 records
            del _job_history[50:]


# ── Startup / Shutdown ────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    init_db()
    start_scheduler()
    logger.info("[Server] AutoClient AI started — dashboard at http://127.0.0.1:8000/")


@app.on_event("shutdown")
async def shutdown() -> None:
    stop_scheduler()


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/", response_class=FileResponse, include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    """Serve the single-file HTML dashboard."""
    if not os.path.exists(_INDEX_HTML):
        raise HTTPException(status_code=404, detail="index.html not found in project root.")
    return FileResponse(_INDEX_HTML, media_type="text/html")


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    """Return server OK + Ollama reachability status."""
    ollama_status = "offline"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://localhost:11434/api/tags")
            if r.status_code == 200:
                ollama_status = "online"
    except Exception:
        pass
    return {"status": "ok", "ollama": ollama_status}


# ── Pipeline control ──────────────────────────────────────────────────────────
@app.post("/scour")
async def scour(req: ScourRequest, bg: BackgroundTasks) -> dict:
    """Kick off the scrape + AI pipeline in the background."""
    if _status["running"]:
        raise HTTPException(status_code=409, detail="Pipeline is already running.")
    bg.add_task(_run_pipeline, req.niche, req.location, req.max_results, req.campaign_id)
    return {"status": "started"}


@app.get("/status")
async def pipeline_status() -> dict:
    """Return live pipeline state (poll every 2 s from the dashboard)."""
    return _status


# ── Leads ─────────────────────────────────────────────────────────────────────
@app.get("/leads")
async def leads(
    status:      Optional[str]   = Query(None),
    niche:       Optional[str]   = Query(None),
    min_score:   float           = Query(0.0),
    q:           Optional[str]   = Query(None),
    campaign_id: Optional[int]   = Query(None),
    include_dnc: bool            = Query(False),
    page:        int             = Query(1, ge=1),
    limit:       int             = Query(50, ge=1, le=1000),
) -> dict:
    return get_leads_filtered(
        status=status,
        niche=niche,
        min_score=min_score,
        q=q,
        campaign_id=campaign_id,
        include_dnc=include_dnc,
        page=page,
        limit=limit,
    )


@app.get("/leads/stats")
async def lead_stats() -> dict:
    return get_stats()


@app.get("/leads/export")
async def export_leads(
    status: Optional[str] = Query(None),
    niche:  Optional[str] = Query(None),
    ids:    Optional[str] = Query(None),
) -> Response:
    """Download leads as CSV. Pass ?ids=1,2,3 to export a selection."""
    id_list = (
        [int(i) for i in ids.split(",") if i.strip().isdigit()]
        if ids else None
    )
    csv_data = export_leads_csv(status=status, niche=niche, ids=id_list)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.put("/leads/{lead_id}")
async def update_lead(lead_id: int, body: LeadUpdateRequest) -> dict:
    """Patch a lead's editable fields (notes, draft, DNC, email, phone, contact)."""
    if not get_lead(lead_id):
        raise HTTPException(status_code=404, detail="Lead not found.")
    # Only send fields that were explicitly provided (not None)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if fields:
        update_lead_partial(lead_id, fields)
    return {"updated": lead_id}


@app.delete("/leads")
async def delete_leads_bulk(body: BulkDeleteRequest) -> dict:
    count = bulk_delete(body.ids)
    return {"deleted": count}


# ── Simulated send ────────────────────────────────────────────────────────────
@app.post("/send/{lead_id}")
async def send_lead(lead_id: int) -> dict:
    """
    Simulate sending a cold email.
    Logs the email to the server console and marks the lead as 'sent'.
    """
    lead = get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    if lead["status"] == "sent":
        return {"message": "Already sent.", "lead_id": lead_id}

    draft   = lead.get("edited_draft") or lead.get("draft") or "(no draft)"
    subject = lead.get("subject_line") or "(no subject)"
    to_addr = lead.get("email") or "(no email)"

    separator = "=" * 60
    logger.info(
        f"\n{separator}\n"
        f"[SEND SIMULATION]\n"
        f"  To      : {to_addr}\n"
        f"  Business: {lead.get('name', '?')}\n"
        f"  Subject : {subject}\n"
        f"  Body    :\n{draft[:500]}{'…' if len(draft) > 500 else ''}\n"
        f"{separator}\n"
    )

    update_lead_status(lead_id, "sent", sent_at=datetime.utcnow())
    return {"message": f"Simulated send → {to_addr}", "lead_id": lead_id}


# ── Config ────────────────────────────────────────────────────────────────────
@app.get("/config")
async def config() -> dict:
    return {
        "model":          MODEL_NAME,
        "sender_name":    SENDER_NAME,
        "sender_company": SENDER_COMPANY,
        "ollama_url":     OLLAMA_URL,
    }


# ── Job history ───────────────────────────────────────────────────────────────
@app.get("/jobs")
async def jobs() -> list:
    """Return up to 10 most-recent completed pipeline runs."""
    return _job_history[:10]


# ── Campaigns ─────────────────────────────────────────────────────────────────
@app.post("/campaigns")
async def create_campaign_route(body: CampaignCreateRequest) -> dict:
    cid = create_campaign(body.name, body.niche, body.location)
    return {"campaign_id": cid}


@app.get("/campaigns")
async def list_campaigns() -> list:
    return get_campaigns()


@app.post("/campaigns/{campaign_id}/send-all")
async def send_all_in_campaign(campaign_id: int) -> dict:
    """Mark all 'processed' leads in a campaign as sent (simulated)."""
    result    = get_leads_filtered(campaign_id=campaign_id, status="processed", limit=500)
    leads_lst = result.get("items", [])
    sent      = 0
    now       = datetime.utcnow()

    for lead in leads_lst:
        if lead.get("email") and lead.get("status") == "processed":
            update_lead_status(lead["id"], "sent", sent_at=now)
            logger.info(
                f"[SEND] Campaign {campaign_id} → {lead.get('email')} / {lead.get('name')}"
            )
            sent += 1

    return {"sent": sent, "campaign_id": campaign_id}


@app.delete("/campaigns/{campaign_id}")
async def archive_campaign_route(campaign_id: int) -> dict:
    archive_campaign(campaign_id)
    return {"archived": campaign_id}


# ── Scheduled jobs ────────────────────────────────────────────────────────────

@app.get("/schedule")
async def list_schedule() -> list:
    """Return all active APScheduler jobs."""
    return list_scheduled_jobs()


@app.post("/schedule")
async def create_schedule(body: ScheduleRequest) -> dict:
    """
    Create (or replace) a recurring scrape job.
    cron_expr: standard 5-field cron string, e.g. "0 9 * * 1" = Monday 09:00 UTC.
    """
    try:
        result = schedule_campaign(
            job_id=body.job_id,
            niche=body.niche,
            location=body.location,
            max_results=body.max_results,
            campaign_id=body.campaign_id,
            cron_expr=body.cron_expr,
            pipeline_fn=_run_pipeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@app.delete("/schedule/{job_id}")
async def delete_schedule(job_id: str) -> dict:
    """Remove a scheduled job by its ID."""
    removed = remove_scheduled_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No job with id={job_id!r}")
    return {"removed": job_id}


# ── Entry point (direct run) ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )

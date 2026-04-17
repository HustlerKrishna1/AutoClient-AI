"""
AutoClient AI — APScheduler integration (Phase 4).

Provides a simple in-process scheduler for recurring scrape campaigns.
Runs inside the FastAPI process — zero extra infrastructure needed.

Usage (from main.py):
    from app.scheduler import start_scheduler, stop_scheduler, schedule_campaign

Scheduled jobs are stored in SQLite via the APScheduler SQLAlchemyJobStore,
so they survive server restarts.
"""

import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

_JOBSTORE_URL = "sqlite:///./autoclient_jobs.db"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        jobstores = {
            "default": SQLAlchemyJobStore(url=_JOBSTORE_URL)
        }
        _scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
    return _scheduler


def start_scheduler() -> None:
    s = get_scheduler()
    if not s.running:
        s.start()
        logger.info("[Scheduler] APScheduler started.")


def stop_scheduler() -> None:
    s = get_scheduler()
    if s.running:
        s.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler stopped.")


def schedule_campaign(
    job_id: str,
    niche: str,
    location: str,
    max_results: int,
    campaign_id: int | None,
    cron_expr: str,          # e.g. "0 9 * * 1"  (Monday 9 AM UTC)
    pipeline_fn,             # pass _run_pipeline from main.py
) -> dict:
    """
    Schedule a recurring scrape job.
    cron_expr: standard 5-field cron string  (minute hour dom month dow)
    """
    s = get_scheduler()

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron_expr must have 5 fields, got: {cron_expr!r}")

    minute, hour, day, month, day_of_week = parts
    trigger = CronTrigger(
        minute=minute, hour=hour,
        day=day, month=month,
        day_of_week=day_of_week,
    )

    s.add_job(
        pipeline_fn,
        trigger=trigger,
        id=job_id,
        replace_existing=True,
        kwargs={
            "niche":       niche,
            "location":    location,
            "max_results": max_results,
            "campaign_id": campaign_id,
        },
    )
    logger.info(f"[Scheduler] Job {job_id!r} scheduled: {cron_expr}")
    return {"job_id": job_id, "cron": cron_expr, "status": "scheduled"}


def list_scheduled_jobs() -> list[dict]:
    s = get_scheduler()
    jobs = []
    for job in s.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger":  str(job.trigger),
        })
    return jobs


def remove_scheduled_job(job_id: str) -> bool:
    s = get_scheduler()
    try:
        s.remove_job(job_id)
        logger.info(f"[Scheduler] Removed job {job_id!r}")
        return True
    except Exception:
        return False

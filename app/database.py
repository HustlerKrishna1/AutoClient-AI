"""
SQLAlchemy SQLite setup for AutoClient AI v2.

New in v2:
  - Extended Lead schema: phone, contact_name, industry, email_source,
    notes, edited_draft, do_not_contact, sent_at, campaign, subject_line, subject_line_b
  - Unique index on (email, niche) — eliminates duplicate leads
  - get_leads_filtered() with status/niche/score/search/pagination
  - get_stats() — aggregate KPIs
  - export_leads_csv() — CSV export (stdlib csv, no new deps)
  - upsert_lead() — INSERT OR IGNORE on (email, niche)
  - delete_lead() / bulk_delete()
  - update_lead_notes() / update_edited_draft() / update_do_not_contact()
  - Campaign table with FK from leads
"""

import csv
import io
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session

DATABASE_URL = "sqlite:///./autoclient.db"

from sqlalchemy import create_engine

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


class Base(DeclarativeBase):
    pass


# ── Campaign ──────────────────────────────────────────────────────────────────

class Campaign(Base):
    __tablename__ = "campaigns"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(255), nullable=False)
    niche      = Column(String(255))
    location   = Column(String(255))
    status     = Column(String(50), default="active")   # active | archived
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Lead ─────────────────────────────────────────────────────────────────────

class Lead(Base):
    __tablename__ = "leads"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(255), nullable=False)
    website        = Column(String(500))
    email          = Column(String(255))
    phone          = Column(String(50))
    contact_name   = Column(String(255))
    industry       = Column(String(100))
    email_source   = Column(String(500))   # URL where email was found
    score          = Column(Float, default=0.0)
    subject_line   = Column(String(500))   # Subject A from AI
    subject_line_b = Column(String(500))   # Subject B variant
    draft          = Column(Text)
    edited_draft   = Column(Text)          # user-edited version
    notes          = Column(Text)
    status         = Column(String(50), default="new")
    do_not_contact = Column(Boolean, default=False)
    niche          = Column(String(255))
    location       = Column(String(255))
    campaign_id    = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    sent_at        = Column(DateTime)

    __table_args__ = (
        # Prevent duplicate leads for the same email in the same niche
        Index("uq_email_niche", "email", "niche", unique=True, sqlite_where=text("email IS NOT NULL")),
    )


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db() -> None:
    Base.metadata.create_all(bind=engine)


# ── Write helpers ─────────────────────────────────────────────────────────────

def save_lead(data: dict) -> int:
    """Insert a new lead. Returns new PK."""
    with Session(engine) as session:
        lead = Lead(**_lead_kwargs(data))
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return lead.id


def upsert_lead(data: dict) -> tuple[int, bool]:
    """
    Insert lead if (email, niche) pair doesn't exist already.
    Returns (id, was_inserted). If duplicate, returns existing id.
    """
    email = data.get("email")
    niche = data.get("niche")

    with Session(engine) as session:
        if email and niche:
            existing = (
                session.query(Lead)
                .filter(Lead.email == email, Lead.niche == niche)
                .first()
            )
            if existing:
                return existing.id, False

        lead = Lead(**_lead_kwargs(data))
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return lead.id, True


def update_lead_ai(lead_id: int, score: float, draft: str, status: str,
                   subject_line: str = "", subject_line_b: str = "") -> None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            row.score          = score
            row.draft          = draft
            row.status         = status
            row.subject_line   = subject_line
            row.subject_line_b = subject_line_b
            session.commit()


def update_lead_status(lead_id: int, status: str, sent_at: datetime | None = None) -> None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            row.status = status
            if sent_at:
                row.sent_at = sent_at
            session.commit()


def update_lead_notes(lead_id: int, notes: str) -> None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            row.notes = notes
            session.commit()


def update_edited_draft(lead_id: int, draft: str) -> None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            row.edited_draft = draft
            session.commit()


def update_do_not_contact(lead_id: int, value: bool) -> None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            row.do_not_contact = value
            session.commit()


def update_lead_partial(lead_id: int, fields: dict) -> None:
    """Generic partial update for any subset of Lead columns."""
    allowed = {
        "notes", "edited_draft", "do_not_contact", "email",
        "phone", "contact_name", "industry", "status", "campaign_id",
    }
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if row:
            for k, v in fields.items():
                if k in allowed:
                    setattr(row, k, v)
            session.commit()


def delete_lead(lead_id: int) -> bool:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def bulk_delete(ids: list[int]) -> int:
    with Session(engine) as session:
        deleted = (
            session.query(Lead)
            .filter(Lead.id.in_(ids))
            .delete(synchronize_session=False)
        )
        session.commit()
        return deleted


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_lead(lead_id: int) -> dict | None:
    with Session(engine) as session:
        row = session.query(Lead).filter(Lead.id == lead_id).first()
        return _to_dict(row) if row else None


def get_leads() -> list[dict]:
    """Return all leads newest-first (legacy; prefer get_leads_filtered)."""
    with Session(engine) as session:
        rows = session.query(Lead).order_by(Lead.created_at.desc()).all()
        return [_to_dict(r) for r in rows]


def get_leads_filtered(
    status: str | None    = None,
    niche: str | None     = None,
    min_score: float      = 0.0,
    q: str | None         = None,
    campaign_id: int | None = None,
    include_dnc: bool     = False,
    page: int             = 1,
    limit: int            = 50,
) -> dict:
    """
    Filtered, paginated lead query.
    Returns {items: [...], total: int, page: int, pages: int}.
    """
    with Session(engine) as session:
        query = session.query(Lead)

        if not include_dnc:
            query = query.filter(
                or_(Lead.do_not_contact == False, Lead.do_not_contact == None)
            )
        if status:
            query = query.filter(Lead.status == status)
        if niche:
            query = query.filter(Lead.niche.ilike(f"%{niche}%"))
        if min_score > 0:
            query = query.filter(Lead.score >= min_score)
        if campaign_id:
            query = query.filter(Lead.campaign_id == campaign_id)
        if q:
            term = f"%{q}%"
            query = query.filter(
                or_(
                    Lead.name.ilike(term),
                    Lead.email.ilike(term),
                    Lead.website.ilike(term),
                    Lead.niche.ilike(term),
                    Lead.location.ilike(term),
                )
            )

        total = query.count()
        offset = (page - 1) * limit
        rows = (
            query.order_by(Lead.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return {
            "items": [_to_dict(r) for r in rows],
            "total": total,
            "page":  page,
            "pages": max(1, -(-total // limit)),  # ceiling division
        }


def get_stats() -> dict:
    """Aggregate KPIs for the dashboard stats bar."""
    with Session(engine) as session:
        total     = session.query(func.count(Lead.id)).scalar() or 0
        sent      = session.query(func.count(Lead.id)).filter(Lead.status == "sent").scalar() or 0
        with_email = session.query(func.count(Lead.id)).filter(Lead.email != None).scalar() or 0
        avg_score = session.query(func.avg(Lead.score)).filter(Lead.score > 0).scalar() or 0.0

        # By status
        status_rows = (
            session.query(Lead.status, func.count(Lead.id))
            .group_by(Lead.status)
            .all()
        )
        by_status = {s: c for s, c in status_rows}

        # By niche (top 10)
        niche_rows = (
            session.query(Lead.niche, func.count(Lead.id))
            .filter(Lead.niche != None)
            .group_by(Lead.niche)
            .order_by(func.count(Lead.id).desc())
            .limit(10)
            .all()
        )
        by_niche = {n: c for n, c in niche_rows}

    return {
        "total":       total,
        "sent":        sent,
        "with_email":  with_email,
        "avg_score":   round(float(avg_score), 1),
        "by_status":   by_status,
        "by_niche":    by_niche,
    }


def export_leads_csv(
    status: str | None = None,
    niche: str | None  = None,
    ids: list[int] | None = None,
) -> str:
    """Return leads as a CSV string. Stdlib csv module — no new dependencies."""
    with Session(engine) as session:
        query = session.query(Lead)
        if status:
            query = query.filter(Lead.status == status)
        if niche:
            query = query.filter(Lead.niche.ilike(f"%{niche}%"))
        if ids:
            query = query.filter(Lead.id.in_(ids))
        rows = query.order_by(Lead.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "website", "email", "phone", "score",
        "subject_line", "status", "niche", "location",
        "notes", "do_not_contact", "created_at", "sent_at",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.name, r.website, r.email, r.phone,
            r.score, r.subject_line, r.status, r.niche, r.location,
            r.notes, r.do_not_contact,
            r.created_at.isoformat() if r.created_at else "",
            r.sent_at.isoformat() if r.sent_at else "",
        ])
    return output.getvalue()


# ── Campaign helpers ──────────────────────────────────────────────────────────

def create_campaign(name: str, niche: str, location: str) -> int:
    with Session(engine) as session:
        c = Campaign(name=name, niche=niche, location=location)
        session.add(c)
        session.commit()
        session.refresh(c)
        return c.id


def get_campaigns() -> list[dict]:
    with Session(engine) as session:
        campaigns = session.query(Campaign).order_by(Campaign.created_at.desc()).all()
        result = []
        for c in campaigns:
            total = session.query(func.count(Lead.id)).filter(Lead.campaign_id == c.id).scalar() or 0
            sent  = session.query(func.count(Lead.id)).filter(
                and_(Lead.campaign_id == c.id, Lead.status == "sent")
            ).scalar() or 0
            avg   = session.query(func.avg(Lead.score)).filter(
                and_(Lead.campaign_id == c.id, Lead.score > 0)
            ).scalar() or 0.0
            result.append({
                "id":         c.id,
                "name":       c.name,
                "niche":      c.niche,
                "location":   c.location,
                "status":     c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "total":      total,
                "sent":       sent,
                "avg_score":  round(float(avg), 1),
            })
        return result


def archive_campaign(campaign_id: int) -> None:
    with Session(engine) as session:
        c = session.query(Campaign).filter(Campaign.id == campaign_id).first()
        if c:
            c.status = "archived"
            session.commit()


# ── Serialisation ──────────────────────────────────────────────────────────────

def _lead_kwargs(data: dict) -> dict:
    allowed = {
        "name", "website", "email", "phone", "contact_name", "industry",
        "email_source", "score", "subject_line", "subject_line_b", "draft",
        "edited_draft", "notes", "status", "do_not_contact", "niche",
        "location", "campaign_id",
    }
    return {k: v for k, v in data.items() if k in allowed}


def _to_dict(row: Lead) -> dict:
    return {
        "id":             row.id,
        "name":           row.name,
        "website":        row.website,
        "email":          row.email,
        "phone":          row.phone,
        "contact_name":   row.contact_name,
        "industry":       row.industry,
        "email_source":   row.email_source,
        "score":          row.score,
        "subject_line":   row.subject_line,
        "subject_line_b": row.subject_line_b,
        "draft":          row.draft,
        "edited_draft":   row.edited_draft,
        "notes":          row.notes,
        "status":         row.status,
        "do_not_contact": row.do_not_contact,
        "niche":          row.niche,
        "location":       row.location,
        "campaign_id":    row.campaign_id,
        "created_at":     row.created_at.isoformat() if row.created_at else None,
        "sent_at":        row.sent_at.isoformat() if row.sent_at else None,
    }

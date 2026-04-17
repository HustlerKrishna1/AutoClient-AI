# AutoClient AI — Local-First Lead Generation & Outreach Automation

> Discover clients. Score them with AI. Write the email. All on your machine. Zero API costs.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-LLaMA_3-black?style=flat-square)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

---

## What It Does

AutoClient AI is a self-contained lead generation engine that runs entirely on your local machine.

You give it a niche and a city. It searches the web, finds real businesses, extracts contact emails, runs them through a local LLM to score their fit and write a personalised cold email — then surfaces everything in a clean dashboard. No subscriptions. No cloud. No rate limits.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     index.html                          │
│            Single-File Dashboard  (Vanilla JS)          │
│   Search Form → Status Polling → Leads Table → Modal    │
└────────────────────────┬────────────────────────────────┘
                         │  HTTP (fetch)
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    FastAPI  app/main.py                  │
│                                                         │
│   POST /scour   ──► Background Pipeline Task            │
│   GET  /status  ──► Real-time Progress                  │
│   GET  /leads   ──► All Leads from DB                   │
│   POST /send/id ──► Simulate Email Send (console log)   │
└──────┬──────────────────────────┬───────────────────────┘
       │                          │
       ▼                          ▼
┌─────────────────┐    ┌──────────────────────────────────┐
│  app/scraper.py │    │        app/processor.py           │
│                 │    │                                   │
│  Playwright     │    │  POST localhost:11434/api/generate│
│  ──────────     │    │  ───────────────────────────────  │
│  DuckDuckGo     │    │  Prompt 1 → Score (JSON 1–10)    │
│  HTML search    │    │  Prompt 2 → Cold email draft      │
│  + email regex  │    │  Handles offline gracefully        │
│  on each site   │    │                                   │
└──────┬──────────┘    └────────────────┬─────────────────┘
       │                                │
       └──────────────┬─────────────────┘
                      ▼
          ┌───────────────────────┐
          │   app/database.py     │
          │                       │
          │   SQLite  autoclient.db│
          │   SQLAlchemy ORM      │
          │   leads table         │
          │   name · website      │
          │   email · score       │
          │   draft · status      │
          └───────────────────────┘
```

---

## Project Structure

```
AutoClient-AI/
├── requirements.txt          # All Python dependencies
├── index.html                # Single-file dashboard (no build step)
└── app/
    ├── __init__.py
    ├── database.py           # SQLite setup, Lead model, CRUD helpers
    ├── scraper.py            # Playwright + BeautifulSoup scraper
    ├── processor.py          # Ollama API client — scoring + email drafts
    └── main.py               # FastAPI server + background pipeline
```

---

## Features

- **Automated lead discovery** — scrapes DuckDuckGo for real businesses by niche and location
- **Email extraction** — visits each business site (and `/contact` page) to find contact emails
- **AI lead scoring** — local LLM rates each lead 1–10 with a reason
- **AI cold email drafting** — generates a personalised, 140-word outreach email per lead
- **Real-time dashboard** — live progress bar, filterable table, score bars, status badges
- **Draft modal** — read and review the AI-written email before sending
- **Simulated send** — logs the full email to the server console (SMTP-ready hook)
- **Graceful offline handling** — marks leads `model_offline` if Ollama is unreachable, never crashes
- **RAM-optimised** — Playwright browser is created and destroyed per search run; safe on 8 GB machines
- **Zero cloud dependency** — 100% local: SQLite, Ollama, Playwright, FastAPI

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Scraping | Playwright (Chromium, headless) + BeautifulSoup4 |
| AI Engine | Ollama — `llama3:8b-instruct-q4_K_M` or `tinyllama` |
| Database | SQLite via SQLAlchemy ORM |
| Frontend | Single-file HTML + Tailwind CSS CDN + Vanilla JS |
| HTTP Client | httpx (async, for Ollama calls) |

---

## Getting Started

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and accessible in PATH
- ~5 GB free disk space (for the LLM model weights)
- 8 GB RAM minimum (16 GB recommended for smooth parallel operation)

---

### 1. Clone & Install

```bash
git clone https://github.com/your-username/AutoClient-AI.git
cd AutoClient-AI

pip install -r requirements.txt
playwright install chromium
```

---

### 2. Start the Local AI

```bash
# Terminal 1 — keep this running
ollama serve

# Terminal 2 — pull the model once (~4.7 GB)
ollama pull llama3:8b-instruct-q4_K_M
```

> **Low RAM?** Use `tinyllama` instead (~600 MB). Change `MODEL_NAME` in `app/processor.py`.

---

### 3. Launch the Server

```bash
# Terminal 2 (or 3)
uvicorn app.main:app --reload --port 8000
```

---

### 4. Open the Dashboard

```
http://localhost:8000
```

Enter a niche (e.g. `wedding photographers`) and a location (e.g. `Austin TX`), choose how many results, and click **Scour**. The pipeline runs in the background — the dashboard polls for live updates automatically.

---

## API Reference

All endpoints return JSON. The dashboard consumes these directly.

### `POST /scour`

Start a scrape + AI enrichment job.

```json
// Request body
{
  "niche": "plumbers",
  "location": "Chicago IL",
  "max_results": 10
}
```

```json
// Response
{
  "status": "started",
  "message": "Scouring 'plumbers' in 'Chicago IL' (10 max)"
}
```

Returns `409` if a job is already running.

---

### `GET /status`

Poll the live pipeline state. Call this every 2 seconds while `running` is `true`.

```json
{
  "running": true,
  "message": "AI processing 4/10: Ace Plumbing & Heating",
  "found": 10,
  "processed": 3,
  "total": 10
}
```

---

### `GET /leads`

Returns all leads in the database, newest first.

```json
[
  {
    "id": 1,
    "name": "Ace Plumbing & Heating",
    "website": "https://aceplumbing.com",
    "email": "info@aceplumbing.com",
    "score": 8.5,
    "draft": "Subject: Quick question about your booking system\n\nHi there...",
    "status": "processed",
    "niche": "plumbers",
    "location": "Chicago IL",
    "created_at": "2026-04-07T14:22:00"
  }
]
```

**Lead statuses:**

| Status | Meaning |
|---|---|
| `new` | Just inserted, not yet AI-processed |
| `processing` | AI pipeline is running for this lead |
| `processed` | Scored and email draft ready |
| `model_offline` | Ollama was unreachable — no score or draft |
| `sent` | `POST /send/{id}` was called |

---

### `POST /send/{id}`

Simulates sending the outreach email. Logs the full draft to the server console and marks the lead `sent`.

```json
// Response
{
  "status": "sent",
  "lead_id": 1,
  "message": "Email logged to console for Ace Plumbing & Heating"
}
```

Returns `404` if the lead ID does not exist.

---

## Pipeline Internals

### Scraping Flow (`app/scraper.py`)

```
1. Build query → "{niche} {location} contact"
2. GET https://html.duckduckgo.com/html/?q={query}  (no JavaScript required)
3. Parse results with BeautifulSoup
   └─ Extract: business name, URL (decode DuckDuckGo redirect), snippet text
4. For each lead without an email found in the snippet:
   └─ Visit homepage  →  regex scan for emails
   └─ Visit /contact  →  regex scan for emails
5. Close and destroy browser  ← RAM freed immediately
6. Return list of raw lead dicts
```

The browser is **always closed in a `finally` block** regardless of errors. Peak Playwright RAM usage is ~250 MB.

---

### AI Processing Flow (`app/processor.py`)

Each lead goes through two sequential Ollama calls:

**Call 1 — Score**
```
Prompt → structured JSON {score: 1–10, reason: "..."}
Parsed with regex fallback if the model outputs non-JSON
```

**Call 2 — Cold Email**
```
Prompt → "Subject: ...\n\nBody (≤140 words)"
Uses the score from Call 1 as context
```

If `ollama serve` is not running, `httpx.ConnectError` is caught and the lead status is set to `model_offline`. The rest of the pipeline continues uninterrupted.

---

## Model Configuration

Default model in `app/processor.py`:

```python
MODEL_NAME = "llama3:8b-instruct-q4_K_M"   # ~4.7 GB, best quality
```

**Alternatives by RAM budget:**

| Model | RAM Required | Quality |
|---|---|---|
| `llama3:8b-instruct-q4_K_M` | ~6 GB | Best |
| `llama3:8b-instruct-q2_K` | ~3.5 GB | Good |
| `tinyllama` | ~1 GB | Baseline |

Pull any model with:
```bash
ollama pull tinyllama
```

Then update `MODEL_NAME` in `app/processor.py` and restart the server.

---

## Dashboard Walkthrough

```
┌──────────────────────────────────────────────────────────────┐
│  AutoClient AI  [Local Edition]              42 leads  API ↗ │
├──────────────────────────────────────────────────────────────┤
│  Find New Leads                                              │
│  ┌──────────────────┐ ┌──────────────────┐ ┌────┐ ┌──────┐ │
│  │ Niche/Service    │ │ Location         │ │ 10 │ │Scour │ │
│  └──────────────────┘ └──────────────────┘ └────┘ └──────┘ │
├──────────────────────────────────────────────────────────────┤
│  ⟳ AI processing 6/10: Downtown Wedding Studio              │
│  ████████████░░░░░░░░  6 / 10 leads processed               │
├──────────────────────────────────────────────────────────────┤
│  Leads          [Filter…] [All statuses ▼] [↺ Refresh]      │
│  ┌────────────────┬──────────────┬───────┬────────┬───────┐ │
│  │ Business       │ Email        │ Score │ Status │ Act.  │ │
│  ├────────────────┼──────────────┼───────┼────────┼───────┤ │
│  │ Ace Plumbing   │ info@ace.com │ 8.5 ██│ Ready  │Draft  │ │
│  │                │              │       │        │ Send  │ │
│  └────────────────┴──────────────┴───────┴────────┴───────┘ │
└──────────────────────────────────────────────────────────────┘
```

**Clicking Draft** opens a modal with the full AI-written email.
**Clicking Send** calls `POST /send/{id}`, logs the email to the server console, and marks the lead sent.
**Score bars** are colour-coded: green ≥ 7, amber ≥ 4, red < 4.
The table auto-refreshes every 8 seconds. The status bar polls every 2 seconds during a live run.

---

## Privacy & Data

- All processing happens on your machine
- No data leaves your network
- No external AI APIs are called
- The SQLite database (`autoclient.db`) lives in the project root
- Delete it at any time to wipe all leads

---

## Use Cases

- **Freelancers** prospecting for new clients in a specific city and niche
- **Agencies** building automated lead pipelines for their sales team
- **Indie hackers** prototyping outreach automation products
- **Researchers** studying local business data and AI-generated copy

---

## Roadmap

- [ ] SMTP / Gmail integration for real email delivery
- [ ] CSV and Notion export
- [ ] Parallel scraping with configurable concurrency
- [ ] Per-lead manual email editing in the dashboard
- [ ] Webhook support for CRM push (HubSpot, Pipedrive)
- [ ] Docker Compose setup for one-command deployment
- [ ] Rate limiting and retry logic for flaky sites
- [ ] Support for additional search engines (Bing, Google via SerpAPI)

---

## Disclaimer

This tool is intended for legitimate business development and research purposes. Always comply with local laws, CAN-SPAM, GDPR, and any applicable email outreach regulations before contacting individuals or businesses. The authors accept no liability for misuse.

---

## License

MIT — free to use, modify, and distribute. Attribution appreciated.

# 🚀 AutoClient AI — Intelligent Lead Generation & Outreach Automation

AutoClient AI is a fast, lightweight system that discovers potential clients, evaluates them using local AI models, and generates personalized outreach emails — all running locally with zero API costs.

---

## 📦 Project Structure

```text
AutoClient-AI/
├── requirements.txt       # Python dependencies
├── index.html             # Single-file dashboard UI
└── app/
    ├── __init__.py
    ├── database.py        # SQLite + SQLAlchemy (lead storage)
    ├── scraper.py         # Playwright + BeautifulSoup (DuckDuckGo scraping)
    ├── processor.py       # Ollama LLM (scoring + email generation)
    └── main.py            # FastAPI backend
```

---

## 🧱 Architecture Design

### 1) Presentation Layer
- **index.html** provides a single-page dashboard UI.
- Connects to backend endpoints for live updates and lead actions.

### 2) API Layer
- **FastAPI** service exposed via `app/main.py`.
- Handles orchestration of scraping, processing, status tracking, and lead retrieval.

### 3) Discovery Layer
- **Playwright + BeautifulSoup** in `app/scraper.py`.
- Performs automated lead discovery from DuckDuckGo search results.

### 4) Intelligence Layer
- **Ollama local LLM** integration in `app/processor.py`.
- Executes lead qualification and personalized cold email generation.

### 5) Persistence Layer
- **SQLite + SQLAlchemy** in `app/database.py`.
- Stores processed leads and supports retrieval for dashboard/API consumption.

---

## ⚡ Features

- 🔍 Automated lead discovery from search engines  
- 🧠 AI-powered lead qualification using local LLMs  
- ✉️ Personalized cold email generation  
- 📊 Real-time dashboard with live updates  
- 💾 Persistent storage with SQLite  
- 🌐 Fully offline AI processing  
- ⚙️ Optimized for low-resource machines  

---

## 🛠️ Tech Stack

- **Backend:** FastAPI  
- **Scraping:** Playwright + BeautifulSoup  
- **Database:** SQLite + SQLAlchemy  
- **AI Engine:** Ollama (LLaMA / TinyLLaMA)  
- **Frontend:** HTML (single-page dashboard)  

---

## 🚀 Getting Started

### 1. Install Dependencies

```bash
cd "C:/Users/newadmin/Projects CC/AutoClient-AI"
pip install -r requirements.txt
playwright install chromium
```

### 2. Start Local AI (Ollama)

```bash
ollama serve
ollama pull llama3:8b-instruct-q4_K_M
```

> Optional: Use a lighter model like `tinyllama` for lower memory usage.

### 3. Run the Server

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. Open Dashboard

```text
http://localhost:8000
```

---

## 🔁 API Endpoints

| Endpoint     | Method | Description              |
| ------------ | ------ | ------------------------ |
| `/scour`     | POST   | Start lead scraping      |
| `/status`    | GET    | Check current progress   |
| `/leads`     | GET    | Retrieve processed leads |
| `/send/{id}` | POST   | Send outreach email      |

---

## 🧠 Model Configuration

Default model:

```text
llama3:8b-instruct-q4_K_M
```

To switch models:

```python
# app/processor.py
MODEL_NAME = "tinyllama"
```

---

## 🔐 Privacy First

- Runs completely on your machine
- No external API calls required
- Full control over your data

---

## 📈 Use Cases

- Lead generation automation
- Cold outreach systems
- Freelancers & agencies
- AI-powered prospecting tools

---

## 🔮 Future Improvements

- Email sending integration (SMTP / Gmail)
- CRM exports (CSV, Notion, Airtable)
- Advanced filtering and scoring
- Parallel scraping engine
- SaaS deployment version

---

## 💡 Vision

> Automate your client acquisition.  
> Replace manual prospecting with intelligent systems.  
> Build leverage using AI.

---

## ⚠️ Disclaimer

Ensure compliance with local laws and email outreach guidelines before using this system.

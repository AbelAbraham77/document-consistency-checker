# PDF Document Consistency Checker

This application uploads PDF documents, extracts and normalizes factual claims, compares related claims for possible contradictions, and presents verified findings with source context through a FastAPI backend and Next.js frontend.

## Setup

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
cd ..
docker compose up -d db
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

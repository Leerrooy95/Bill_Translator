# CLAUDE.md — Bill Translator Development Guide

> **What this file is:** Instructions for AI assistants (Claude, Copilot, etc.) and
> human developers working on this repository. Read this first.

---

## What This Project Does

Bill Translator rewrites legislative bills at an **8th-grade reading level** using
Claude by Anthropic. It scores text with the Flesch-Kincaid formula, detects
meaning drift in legal terms, and lets users iterate until the readability target
is met. It also supports **PDF ingestion** and **fact-checking claims** in bills
against web sources via Brave Search.

---

## Quick Start

### Option A — GitHub Codespaces (fastest)

1. Click **Code → Codespaces → Create codespace on main**.
2. Once the terminal loads:
   ```bash
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env and paste your ANTHROPIC_API_KEY
   python web_app.py
   ```
3. Codespaces auto-forwards port 5000 — click the link in the terminal.

### Option B — Local (any OS)

```bash
git clone https://github.com/Leerrooy95/Bill_Translator.git
cd Bill_Translator
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your ANTHROPIC_API_KEY
python3 web_app.py
```

Open **http://localhost:5000** in your browser.

### Option C — Docker (optional)

```bash
docker build -t bill-translator .
docker run -p 5000:5000 --env-file .env bill-translator
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes* | — | Claude API key. *Can also be entered in the browser (BYOK).* |
| `BRAVE_SEARCH_API_KEY` | No | — | Brave Search key for fact-checking. Entered in-browser if not set. |
| `FLASK_SECRET_KEY` | No | random | Set for persistent sessions across restarts. |
| `FLASK_DEBUG` | No | `false` | Set `true` for development auto-reload. |
| `PORT` | No | `5000` | HTTP port for the web server. |

Copy `.env.example` to `.env` and fill in your values. **Never commit `.env`.**

---

## Project Structure

```
Bill_Translator/
├── web_app.py               ← Flask web UI (main entry point)
├── translator_agent.py      ← CLI tool + core translation engine
├── config.py                ← Centralized Flask configuration
├── document_processor.py    ← PDF/txt/md ingestion, secure uploads
├── fact_checker.py          ← Brave Search + Claude claim verification
├── tests.py                 ← Test suite (unittest)
├── requirements.txt         ← Python dependencies
├── .env.example             ← Environment variable template
├── .gitignore               ← Security-focused ignore rules
├── CLAUDE.md                ← This file
├── README.md                ← User-facing documentation
├── LICENSE                  ← GPL v2
├── templates/
│   ├── index.html           ← Upload page
│   └── results.html         ← Side-by-side comparison
├── Example_Documents/       ← Sample bills for testing
│   ├── BALLOT.txt
│   └── README.md
└── .github/
    └── workflows/
        └── validate.yml     ← CI: compile, test, secrets scan
```

---

## Running Tests

```bash
python3 -m unittest tests -v
```

This runs all tests including readability scoring, legal term extraction, drift
detection, document processing, fact-checker mocking, and web interface routes.

---

## Running the CLI

```bash
# Translate a single bill
python3 translator_agent.py path/to/bill.txt

# Score-only (no API call)
python3 translator_agent.py --score-only path/to/bill.txt

# Preserve legal terms
python3 translator_agent.py bill.txt --preserve-legal-terms

# Auto re-iterate up to 3 times
python3 translator_agent.py bill.txt --max-iterations 3

# Batch mode (all .txt files in raw_legislation/)
python3 translator_agent.py
```

---

## Architecture Notes

### Translation Pipeline
1. User uploads text (`.txt`, `.pdf`, `.md`) or pastes directly
2. `document_processor.py` extracts text (pdfplumber for PDFs)
3. `translator_agent.py` sends text to Claude with readability-aware prompts
4. Flesch-Kincaid scoring (`textstat`) evaluates the result
5. Legal term drift detection compares original vs. translated terms
6. User can re-iterate, accept, or reject

### Fact-Checking Pipeline
1. User submits a claim from a bill
2. `fact_checker.py` searches Brave Search for evidence
3. Claude analyzes the evidence and returns a verdict
4. Verdict: VERIFIED / UNVERIFIED / CONTRADICTED / INSUFFICIENT_DATA

### Security Model
- **BYOK (Bring Your Own Key)** — API keys are entered per-session, never stored on disk
- **Security headers** — CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **File upload safety** — extension whitelist, size limits, hashed filenames for uploads
- **Path traversal protection** — all file paths are validated and resolved
- **No hardcoded secrets** — CI scans for accidental secret commits
- **Session cookies** — HttpOnly, SameSite=Lax

---

## Code Conventions

- **Python 3.8+** compatible (no walrus operators in core paths)
- **Flask** for web, **Jinja2** for templates, **Bootstrap 5** for CSS
- Functions use **snake_case**, classes use **PascalCase**
- All public functions have **docstrings**
- Tests use **unittest** (stdlib) — no pytest dependency required
- Configuration is centralized in `config.py`, loaded from environment variables
- File operations validate paths to prevent traversal attacks

---

## CI / Continuous Integration

The `.github/workflows/validate.yml` workflow runs on every push and PR to `main`:

1. **Compile check** — `py_compile` on all Python files
2. **Run tests** — `python -m unittest tests -v`
3. **Secrets scan** — grep for hardcoded API keys or tokens
4. **Template validation** — ensure all required HTML templates exist

---

## Deployment

### Render (Free Tier)

The app deploys to Render with zero config:
- Push to GitHub → connect repo on Render
- Set `FLASK_SECRET_KEY` as an environment variable
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn web_app:app --bind 0.0.0.0:$PORT --timeout 120`

### Production Checklist

- [ ] Set `FLASK_SECRET_KEY` to a strong random value
- [ ] Use HTTPS (Render provides this automatically)
- [ ] Consider rate limiting for the `/upload` and `/fact-check` endpoints
- [ ] Set `FLASK_DEBUG=false` (default)

---

## Adding New Features

When adding features, follow these patterns:

1. **New processing module** — create a standalone `.py` file (see `fact_checker.py`)
2. **New route** — add to `web_app.py`, import from your module
3. **New tests** — add a test class in `tests.py`
4. **New dependency** — add to `requirements.txt` with minimum version pin
5. **New env var** — document in `.env.example` and this file

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "ANTHROPIC_API_KEY not found" | Create `.env` from `.env.example` and add your key |
| PDF upload fails | Ensure `pdfplumber` is installed: `pip install pdfplumber` |
| Fact-check returns "No API key" | Enter Brave Search API key in the web UI |
| Tests fail on import | Run `pip install -r requirements.txt` first |
| Port already in use | Set `PORT=5001` in `.env` or kill the existing process |

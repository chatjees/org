# ChatJEEs

An AI tutor web app for students preparing for **JEE Main** and **JEE Advanced**. It runs on Claude through the Anthropic API.

## Features

| Mode | What it does |
|---|---|
| 🧩 **Solve a doubt** | Step-by-step solution: concept, given data, working, final answer with units, and the common trap. Works with a typed question or a **photo of the question**. |
| 💡 **Explain a concept** | Intuition first, then formulas, plus one worked example each at Main and Advanced level, with the NCERT chapter reference. |
| 📝 **Practice** | Gives one original JEE-style problem at a time, grades your attempt, finds the step where you went wrong, and adjusts difficulty. |
| 🗓️ **Study plan** | A week-by-week plan based on your exam date, daily hours and weak chapters. |

- Subject focus: Physics / Chemistry / Mathematics / All
- Answers stream live; maths is rendered with KaTeX and Markdown is sanitised
- Mobile-friendly, with light and dark themes
- Large phone photos are downscaled in the browser before upload

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                    # get one at https://console.anthropic.com
python app.py
```

Open <http://localhost:5000>.

## Configuration (environment variables)

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. Keep it on the server and never put it in front-end code. |
| `CHATJEES_MODEL` | `claude-opus-5-5` | Claude model ID |
| `CHATJEES_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max`: more effort means more careful reasoning but costs more |
| `CHATJEES_MAX_TOKENS` | `64000` | Upper limit on reply length |
| `PORT` | `5000` | |

## Project layout

```
app.py                 Flask server: /, /api/chat (SSE stream), /api/health
chatjees/prompts.py    Tutor system prompt, subjects and modes
templates/index.html   Page
static/app.js          Chat state, streaming, Markdown + KaTeX rendering, image upload
static/style.css       Styles
tests/test_app.py      Offline tests (Claude client is mocked)
```

## Deploy

**Render (easiest):** push this repo to GitHub, then on render.com choose *New → Blueprint*, select the repo, and paste your `ANTHROPIC_API_KEY` when asked. `render.yaml` does the rest.

**Any Docker host** (Railway, Fly.io, Google Cloud Run, an AWS/Azure VM):

```bash
docker build -t chatjees .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... chatjees
```

Every question is billed to your API key. Before sharing the URL publicly, add a login and per-user rate limits.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Notes

- The server keeps no state. The browser sends the chat history with each request, and an image is sent only with the turn it was attached to.
- If Claude's safety classifier declines a request, the server-side fallback (`fallbacks: "default"`) retries it on Anthropic's recommended fallback model.
- For production, run it behind a WSGI server, for example `gunicorn -w 4 -k gthread --threads 8 app:app`, and add authentication and rate limiting before going public, because every request costs API credit.

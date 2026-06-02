# CareerMaster AI

**CV-aware interview preparation that runs in your browser.** Upload your CV and
CareerMaster reads it, runs a tailored mock interview, and listens while you
speak to coach your answers in real time using the STAR method.

The web app works **100% client-side** — no server required — so it can be
hosted for free on GitHub Pages. An optional FastAPI backend (included) upgrades
the experience with real LLM-generated questions, answer evaluation, and live
suggestions when you deploy it.

> **Live demo:** https://AYKHAN-USERNAME.github.io/careermaster-ai/
> *(replace with your own GitHub Pages URL once published — see Deployment below)*

---

## Features

**1 · CV Analysis** — Drop in a PDF or DOCX. It's parsed in the browser with
pdf.js / mammoth.js. CareerMaster extracts your contacts, skills, and experience
signals, scores ATS-readiness out of 100, lists concrete improvement
suggestions, and exports structured JSON.

**2 · Mock Interview** — Pick a target role and difficulty. Questions are built
from the skills detected in your CV and span behavioural, technical, and
role-fit categories. Navigate freely between questions, type an answer, and get
it scored against the four **STAR** dimensions (Situation, Task, Action, Result)
with specific feedback.

**3 · Live Interview Assistant** — Using the browser's Web Speech API, the
assistant transcribes the interviewer's questions live, detects when a question
is being asked, and surfaces a STAR-shaped prompt anchored on your own CV. A mic
test, live input-level meter, and an end-of-session summary are included.

---

## Tech stack

| Layer | Technology |
| ----- | ---------- |
| Frontend | Vanilla HTML / CSS / JavaScript (no build step) |
| In-browser parsing | pdf.js, mammoth.js |
| Speech | Web Speech API (`SpeechRecognition`) |
| Optional backend | FastAPI, Uvicorn |
| NLP / parsing | spaCy (`en_core_web_sm`), pdfplumber, python-docx |
| LLM | Llama 3.3 via Groq |
| Deployment | GitHub Pages (frontend) · Docker → Fly.io / Render (backend) |

---

## How it works

The frontend is designed around **graceful degradation**:

- With **no backend configured**, every feature runs locally in the browser —
  CV parsing, rule-based question generation, and STAR heuristics.
- If you set `BACKEND_URL` in `assets/js/config.js` to a deployed API, the app
  probes `/health` on load and, when it's reachable, automatically routes
  question generation, answer evaluation, and live suggestions through the LLM
  backend instead.

This means the public demo always works, while the full AI experience is a
drop-in upgrade.

---

## Project structure

```
careermaster-ai/
├── index.html                # the single-page app
├── assets/
│   ├── css/styles.css         # minimalist dark theme
│   └── js/
│       ├── config.js          # optional BACKEND_URL
│       └── app.js             # CV analysis · mock interview · live assistant
├── backend/                   # optional FastAPI API (the "AI" upgrade)
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── fly.toml               # Fly.io deploy config
│   ├── render.yaml            # Render deploy config
│   ├── .env.example
│   └── tests/                 # pytest suite
├── README.md
└── LICENSE
```

---

## Running locally

### Frontend (all you need for the full in-browser app)

Because the speech and file APIs need a real origin, serve the folder over HTTP
rather than opening the file directly:

```bash
# from the repository root
python -m http.server 5500
# then open http://localhost:5500
```

Use **Chrome or Edge** for the live assistant (Web Speech API support).

### Backend (optional — enables LLM features)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your GROQ_API_KEY
uvicorn main:app --reload --port 8001
```

Then set `BACKEND_URL: "http://localhost:8001"` in `assets/js/config.js` and
reload the page — the AI status badge in the hero will switch to **Online**.

Run the backend tests with:

```bash
cd backend && pytest
```

---

## Deployment

### Frontend → GitHub Pages (free)

1. Push this repository to GitHub.
2. In the repo, go to **Settings → Pages**.
3. Under **Build and deployment**, set **Source: Deploy from a branch**, pick
   the `main` branch and the `/ (root)` folder, and **Save**.
4. After a minute your site is live at
   `https://<your-username>.github.io/careermaster-ai/`.

### Backend → Fly.io (optional)

```bash
cd backend
fly launch --no-deploy        # creates the app from fly.toml
fly secrets set GROQ_API_KEY=your_key_here
fly deploy
```

Then point `BACKEND_URL` in `config.js` at the resulting
`https://<app>.fly.dev` URL and redeploy the frontend. Render works the same way
via `render.yaml`.

---

## Privacy

CV files are parsed **in the browser** and stored only in your own
`localStorage`; nothing is uploaded unless you explicitly deploy and connect the
optional backend.

## License

Released under the [MIT License](LICENSE).

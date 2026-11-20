# Litera

**Ask a research question. Get a literature review with linked academic sources.** Litera is a web app for students and early researchers, built around a simple chat-style workspace.

[Live website](https://xronitx.github.io/litera-research-assistant/) · [Source code](https://github.com/xRONITx/litera-research-assistant)

> **Deployment status:** GitHub Pages hosts the frontend. Live research and sign-in become available after the API, model provider, and Supabase settings in [Deployment setup](#deployment-setup) are configured. The app never includes a model API key in browser code. Free model accounts have usage limits; the shared service is capped at three reviews per signed-in user each day.

## What it does

- Accepts custom research questions.
- Searches academic papers with Semantic Scholar.
- Retrieves relevant abstracts and drafts a structured synthesis with linked citations.
- Checks the draft once for coverage and citation issues, then can revise it once.
- Shows the papers used and lets users copy or revisit reviews in the same browser.
- Supports email sign-in and sign-up through Supabase Auth.
- Keeps Gemini and Groq API keys on the FastAPI server.

The results are a first pass for research. Always check the original papers before citing them. The app currently searches paper records and abstracts; it does not retrieve paywalled full text or accept uploaded papers.

## Built with

| Part | Technology |
|---|---|
| Web app | React, TypeScript, Vite |
| API | FastAPI, Pydantic, Uvicorn |
| Research workflow | LangGraph |
| Paper search | Semantic Scholar |
| Retrieval | ChromaDB |
| Language models | Gemini Flash or Groq Llama, with optional local Ollama |
| Sign-in | Supabase Auth |
| Hosting | GitHub Pages (web), Render (API template) |

## Run locally

Requirements: Node.js 20+, Python 3.11+, and a free Gemini or Groq API key. Local sign-in is optional by default.

### Start the API

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example backend/.env
```

Add `GEMINI_API_KEY` or `GROQ_API_KEY` to `backend/.env`. Gemini is tried first by default; Groq is used as a fallback when configured. Then start the API from the repository root:

```powershell
uvicorn backend.main:app --reload --port 8000
```

API documentation: [localhost:8000/docs](http://localhost:8000/docs) · Health: [localhost:8000/health](http://localhost:8000/health)

### Start the web app

In another terminal, from the repository root:

```powershell
npm install
Copy-Item .env.example .env
npm run dev
```

Open [localhost:5173](http://localhost:5173), enter any research question, and press Enter or click the arrow.

To enable sign-in locally, create a Supabase project, set `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and `VITE_REQUIRE_AUTH=true` in the frontend `.env`, then set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `REQUIRE_AUTH=true` in `backend/.env`. For local development without Supabase, leave `VITE_REQUIRE_AUTH` and `REQUIRE_AUTH` false.

## API routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Read API, sign-in, and model readiness flags |
| `POST` | `/query` | Start a review; requires a valid Supabase session when auth is enabled |
| `GET` | `/status/{job_id}` | Read progress for the signed-in user's run |
| `GET` | `/report/{job_id}` | Fetch that user's completed review and sources |

Example local request:

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"How does sleep quality affect academic performance in university students?"}'
```

When sign-in is enabled, send `Authorization: Bearer <supabase-access-token>` with the request. Job status and report routes are scoped to the authenticated user.

## Deployment setup

### 1. Create the free-tier service accounts

- Create a Supabase project and enable email/password authentication.
- Create a Gemini API key or a Groq API key. Keep the key private; do not put it in a `VITE_` variable or commit it.
- Import the repository into Render and create the API service from `render.yaml`.

### 2. Configure the API on Render

Set these service environment variables in Render:

| Variable | Value |
|---|---|
| `GEMINI_API_KEY` or `GROQ_API_KEY` | Your provider's server-side API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase publishable/anon key |
| `REQUIRE_AUTH` | `true` |
| `DAILY_RESEARCH_LIMIT` | `3` |
| `CORS_ORIGINS` | `https://xronitx.github.io` |

The deployment tries Gemini first and Groq second when both are configured. Provider free tiers and model names can change. Each signed-in user is limited to three requests per rolling day to preserve the shared free quota.

### 3. Configure the GitHub Pages frontend

The [Pages workflow](.github/workflows/pages.yml) builds and publishes the website on pushes to `main`. In the GitHub repository, add these **Actions variables** under **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
|---|---|
| `VITE_API_URL` | The Render service origin, such as `https://litera-api.onrender.com` |
| `VITE_SUPABASE_URL` | The same Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | The same Supabase publishable/anon key |
| `VITE_REQUIRE_AUTH` | `true` |

The Supabase anon key is intended for browser use; enable Supabase email confirmation and configure allowed redirect URLs to include `https://xronitx.github.io/litera-research-assistant/`. Model API keys belong only in Render's protected environment settings.

After setting the variables, rerun **Deploy site to GitHub Pages** from the repository's Actions tab. The frontend is static; it cannot run research until `VITE_API_URL` points to a deployed API with valid provider credentials.

## Important deployment limits

- Render free services may sleep when idle, so the first request can take longer.
- Job state is currently held in API process memory. A restart can lose active reviews; add durable storage before relying on saved server-side history.
- User review history is stored in that browser, separated by signed-in account. It does not yet sync between devices.
- Shared model keys have provider quotas. The per-user limit is a basic guard; production use should add durable rate limiting and usage monitoring.
- Semantic Scholar results can be incomplete, and many records only expose abstracts.
- Evaluation has not been run. No RAGAS scores or baseline comparisons are claimed. See [the evaluation plan](docs/evaluation.md).

## Project progress

- [x] Chat-style research interface and custom question entry
- [x] FastAPI query, status, report, and health routes
- [x] Academic search, vector retrieval, synthesis, and bounded review pass
- [x] Gemini/Groq provider adapter with server-side keys
- [x] Supabase email sign-in and per-user report access
- [x] GitHub Pages deployment workflow and public frontend
- [ ] Configure the Render API and provider credentials
- [ ] Configure Supabase and connect the hosted frontend
- [ ] Measure RAGAS results and publish the evaluation report
- [ ] Add durable job storage and a demo video

## License

MIT. See [LICENSE](LICENSE).

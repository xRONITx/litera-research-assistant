# Project progress

Updated: 2026-11-20

## Implemented

- Responsive chat-style React + TypeScript research interface for custom questions.
- Email sign-in/sign-up integration with Supabase Auth and per-account browser history.
- Per-user API job/report authorization and a three-reviews-per-day default cap.
- FastAPI background jobs with query, status, report, and health routes.
- LangGraph planner → academic researcher → RAG analyst → critic workflow, including one bounded revision.
- Semantic Scholar search and ChromaDB local retrieval.
- Gemini 3.5 Flash-Lite primary, Groq fallback, optional Ollama local provider.
- Environment templates and Vercel, Render, and Docker deployment configuration.
- Public GitHub repository and deployed GitHub Pages demo.
- Architecture, setup, limitations, and evaluation status documented.

## Remaining

- Create/configure the free Supabase, Gemini/Groq, and Render services; set their project values in GitHub Pages variables and Render secrets.
- Complete a live custom-question run after those service settings are in place.
- Run the planned RAGAS evaluation and publish measured results.
- Add durable job persistence for production use.
- Record a demo video after deployment.

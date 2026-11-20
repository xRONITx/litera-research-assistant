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
- Public GitHub repository and deployed GitHub Pages frontend.
- Render API deployed with a Gemini key stored as a server environment secret.
- Supabase email authentication configured with the production redirect URL.
- GitHub Pages build variables configured; API health reports the service, auth, and model as ready.
- Architecture, setup, limitations, and evaluation status documented.

## Remaining

- Complete the first signed-in custom-question run after a user creates an account and confirms email.
- Run the planned RAGAS evaluation and publish measured results.
- Add durable job persistence for production use.
- Record a demo video after deployment.

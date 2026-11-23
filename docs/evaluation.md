# Litera RAGAS evaluation

## What is implemented

Litera now has a reproducible live evaluation runner at [`scripts/evaluate_ragas.py`](../scripts/evaluate_ragas.py), a six-case curated benchmark at [`evaluation/cases.jsonl`](../evaluation/cases.jsonl), and machine-readable plus Markdown output under `evaluation/results/` after a run. The runner sends each case to the configured Litera API, waits for the completed review, scores the answer and retrieved title/abstract contexts with RAGAS, then saves the exact inputs, per-case scores, aggregates, provider/model, API revision, package versions, and limitations.

The three implemented metrics are:

- **Faithfulness:** whether answer claims are supported by the retrieved contexts.
- **Answer relevance:** whether the answer addresses the question; uses Gemini embeddings as required by this metric.
- **Context precision:** whether retrieved contexts are relevant to a curated reference answer, accounting for ranking order.

All scores range from 0 to 1, with higher values indicating better performance for the measured property. These are model-based evaluation signals, not a single accuracy or quality score. The context precision references and their sources are curated and versioned with the benchmark. The benchmark spans remote work, adolescent sleep, LLM hallucinations, early reading, and household food waste.

## Run the benchmark

Use Python 3.10 or later. The production API defaults to three new reviews per user per rolling 24 hours, so the runner evaluates three cases by default. A complete six-case run requires an API/configuration with enough quota (for example, a local development service configured with a higher `DAILY_RESEARCH_LIMIT`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-evaluation.txt
```

Set the evaluator key in the local shell or an ignored `.env` file as `RAGAS_GOOGLE_API_KEY`. The runner also accepts `GOOGLE_API_KEY` or `GEMINI_API_KEY` as a fallback. Set `LITERA_API_URL` to the API base URL; it defaults to the production Litera API. For an authenticated deployment, set the signed-in Supabase access token as `LITERA_ACCESS_TOKEN`. Do not commit `.env` files or paste tokens into source files.

```powershell
$env:RAGAS_GOOGLE_API_KEY = "your-evaluation-key"
$env:LITERA_API_URL = "https://litera-api-9rml.onrender.com"
$env:LITERA_ACCESS_TOKEN = "your-supabase-session-token"
python scripts/evaluate_ragas.py
```

The token is only needed for APIs with `REQUIRE_AUTH=true`. For local evaluation, point `LITERA_API_URL` at a local Litera API instance. To evaluate all six cases, use `python scripts/evaluate_ragas.py --limit 0` only when the target API quota allows six new reviews. `--timeout` controls the per-review wait; `--judge-model` overrides the default Gemini judge model. The RAGAS judge uses Google Gemini and the answer-relevance embedding model defaults to `gemini-embedding-001`.

The run writes:

- `evaluation/results/latest.json` — raw questions, references, generated answers, retrieved contexts, per-case scores, aggregates, metadata, and any metric-level errors.
- `evaluation/results/latest.md` — a compact scorecard suitable for reports and presentations.
- `public/evaluation-scores.json` — aggregate and per-question scores without generated answer text or retrieved abstracts; the Litera website reads this file.

These outputs are deliberately generated from a live run. They should only be described as measured results after the command completes successfully; failed metrics remain `N/A`, never zero. Commit and push `public/evaluation-scores.json` to publish the scorecard to GitHub Pages. The website's **Evaluation** button is visible from the workspace and sign-in screen, and displays a pending state until measured data is published.

## Current result status

No scores are published yet. This workspace does not have a RAGAS judge key or authenticated API session configured, so I could not run the live benchmark here. The implementation is ready to generate the validation scorecard once those local credentials and API quotas are available. This is not a claim that the RAGAS evaluation has already passed.

## Reproducibility and interpretation

- The cases and references are checked into source control. The runner records the exact Litera answer and retrieved title/abstract text scored for each case.
- Results capture the Litera API revision and RAGAS / Google SDK / Python versions. Gemini judge and embedding model are configurable through `RAGAS_JUDGE_MODEL` and `RAGAS_EMBEDDING_MODEL`.
- The hosted API may sleep, encounter academic-index limits, or enforce its rolling review cap. A failed query is reported as a run failure rather than a fabricated score.
- Six curated questions are a small convenience benchmark. Scores should not be generalized to all subjects, queries, or users.
- LLM judge scores can vary with judge model/version and are not a substitute for expert review or ground-truth study-level annotation.
- Litera commonly receives abstracts rather than full paper text; faithfulness measures support from the context Litera actually supplied, not the full paper.
- The benchmark evaluates the deployed multi-agent flow only. It does not claim improvement over a baseline.

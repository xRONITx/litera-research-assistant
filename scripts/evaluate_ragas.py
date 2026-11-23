"""Run Litera's live literature-review benchmark and score it with RAGAS."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evaluation" / "cases.jsonl"
RESULTS_DIR = ROOT / "evaluation" / "results"


def load_cases(limit: int) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit > 0:
        cases = cases[:limit]
    if not cases:
        raise ValueError("The evaluation case file is empty.")
    for case in cases:
        for field in ("case_id", "question", "reference", "reference_sources"):
            if not case.get(field):
                raise ValueError(f"Evaluation case is missing {field!r}: {case.get('case_id', '<unknown>')}")
    return cases


async def run_live_case(client: httpx.AsyncClient, base_url: str, headers: dict[str, str], case: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    response = await client.post(f"{base_url}/query", headers=headers, json={"question": case["question"]})
    if response.status_code != 202:
        detail = response.text[:600]
        raise RuntimeError(f"Litera rejected {case['case_id']} (HTTP {response.status_code}): {detail}")
    job_id = response.json().get("job_id")
    if not job_id:
        raise RuntimeError(f"Litera returned no job ID for {case['case_id']}.")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status_response = await client.get(f"{base_url}/status/{job_id}", headers=headers)
        status_response.raise_for_status()
        status = status_response.json()
        if status.get("status") == "completed":
            report_response = await client.get(f"{base_url}/report/{job_id}", headers=headers)
            report_response.raise_for_status()
            report = report_response.json()
            contexts = []
            for source in report.get("sources", []):
                title = str(source.get("title") or "").strip()
                abstract = str(source.get("abstract") or "").strip()
                context = f"{title}\n{abstract}".strip()
                if context:
                    contexts.append(context)
            answer = str(report.get("review") or "").strip()
            if not answer:
                raise RuntimeError(f"Litera returned an empty answer for {case['case_id']}.")
            if not contexts:
                raise RuntimeError(f"Litera returned no usable title/abstract contexts for {case['case_id']}.")
            return {"response": answer, "retrieved_contexts": contexts, "source_count": len(report.get("sources", [])), "generated_at": report.get("generated_at"), "critic": report.get("critic", {})}
        if status.get("status") == "failed":
            raise RuntimeError(f"Litera failed {case['case_id']}: {status.get('error', 'unknown error')}")
        await asyncio.sleep(3)
    raise TimeoutError(f"Litera did not finish {case['case_id']} within {timeout_seconds} seconds.")


async def score_case(metrics: dict[str, Any], case: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    arguments = {
        "user_input": case["question"],
        "response": answer["response"],
        "retrieved_contexts": answer["retrieved_contexts"],
    }
    outputs: dict[str, Any] = {}
    for name, metric in metrics.items():
        try:
            metric_arguments = dict(arguments)
            if name == "context_precision":
                metric_arguments["reference"] = case["reference"]
            value = (await metric.ascore(**metric_arguments)).value
            outputs[name] = float(value) if value is not None else None
        except Exception as exc:  # preserve the other metric scores if a provider quota is hit
            detail = str(exc)
            for secret_name in ("RAGAS_GOOGLE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "LITERA_ACCESS_TOKEN"):
                secret = os.getenv(secret_name)
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            outputs[name] = {"error": f"{type(exc).__name__}: {detail[:400]}"}
    return outputs


async def evaluate(args: argparse.Namespace) -> int:
    try:
        import httpx
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("Evaluation dependencies are missing. Install them with: pip install -r requirements-evaluation.txt") from exc
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "backend" / ".env", override=False)
    key = os.getenv("RAGAS_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set RAGAS_GOOGLE_API_KEY (or GOOGLE_API_KEY/GEMINI_API_KEY) locally before running the evaluator.")

    try:
        from google import genai
        from ragas.embeddings import GoogleEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, Faithfulness
    except ImportError as exc:
        raise RuntimeError("Evaluation dependencies are missing. Install them with: pip install -r requirements-evaluation.txt") from exc

    cases = load_cases(args.limit)
    base_url = (args.api_url or os.getenv("LITERA_API_URL") or "https://litera-api-9rml.onrender.com").rstrip("/")
    token = os.getenv("LITERA_ACCESS_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    model = args.judge_model or os.getenv("RAGAS_JUDGE_MODEL", "gemini-2.5-flash")
    embedding_model = os.getenv("RAGAS_EMBEDDING_MODEL", "gemini-embedding-001")

    genai_client = genai.Client(api_key=key)
    evaluator_llm = llm_factory(model, provider="google", client=genai_client)
    evaluator_embeddings = GoogleEmbeddings(client=genai_client, model=embedding_model)
    metrics = {
        "faithfulness": Faithfulness(llm=evaluator_llm),
        "answer_relevance": AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
        "context_precision": ContextPrecision(llm=evaluator_llm),
    }

    timeout = httpx.Timeout(connect=20, read=120, write=30, pool=20)
    async with httpx.AsyncClient(timeout=timeout) as client:
        health_response = await client.get(f"{base_url}/health")
        health_response.raise_for_status()
        health = health_response.json()
        if health.get("model_configured") != "true":
            raise RuntimeError("The selected Litera API has no configured model provider.")
        rows = []
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] Generating Litera review: {case['case_id']}", flush=True)
            answer = await run_live_case(client, base_url, headers, case, args.timeout)
            print(f"[{index}/{len(cases)}] Scoring RAGAS metrics", flush=True)
            scores = await score_case(metrics, case, answer)
            rows.append({
                "case_id": case["case_id"],
                "question": case["question"],
                "reference": case["reference"],
                "reference_sources": case["reference_sources"],
                **answer,
                "scores": scores,
            })

    metric_names = list(metrics)
    aggregates: dict[str, float | None] = {}
    for name in metric_names:
        values = [row["scores"].get(name) for row in rows]
        usable = [v for v in values if isinstance(v, (int, float))]
        aggregates[name] = mean(usable) if usable else None

    try:
        ragas_version = importlib.metadata.version("ragas")
        genai_version = importlib.metadata.version("google-genai")
    except importlib.metadata.PackageNotFoundError:
        ragas_version = genai_version = "unknown"
    payload = {
        "status": "complete" if all(value is not None for value in aggregates.values()) else "partial",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "system": "Litera deployed API",
        "api_url": base_url,
        "api_revision": health.get("revision", "unknown"),
        "judge_provider": "Google Gemini",
        "judge_model": model,
        "embedding_model": embedding_model,
        "versions": {"python": sys.version.split()[0], "ragas": ragas_version, "google-genai": genai_version},
        "case_count": len(rows),
        "metrics": aggregates,
        "cases": rows,
        "limitations": [
            "A small curated benchmark is not a representative estimate of all user questions.",
            "RAGAS scores are model-based judgments and may vary by judge model/version.",
            "Litera retrieves bibliographic records and abstracts, not necessarily full papers.",
            "The three scores measure different properties and should not be read as a single accuracy score.",
        ],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / "latest.json"
    md_path = RESULTS_DIR / "latest.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"\nRAGAS results written to:\n  {json_path}\n  {md_path}")
    for name, value in aggregates.items():
        print(f"{name}: {'N/A' if value is None else f'{value:.4f}'}")
    return 0 if payload["status"] == "complete" else 2


def render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    rows = [
        "# Litera RAGAS evaluation results",
        "",
        f"- **Status:** {payload['status'].title()}",
        f"- **Evaluated:** {payload['evaluated_at']}",
        f"- **System/API revision:** {payload['system']} / `{payload['api_revision']}`",
        f"- **Cases:** {payload['case_count']}",
        f"- **Judge:** {payload['judge_provider']} `{payload['judge_model']}`",
        f"- **Embeddings:** `{payload['embedding_model']}`",
        f"- **Versions:** Python `{payload['versions']['python']}`, RAGAS `{payload['versions']['ragas']}`, google-genai `{payload['versions']['google-genai']}`",
        "",
        "## Aggregate scores",
        "",
        "| Metric | Mean score | Scored cases |",
        "|---|---:|---:|",
    ]
    for metric, value in metrics.items():
        scored = sum(isinstance(row["scores"].get(metric), (int, float)) for row in payload["cases"])
        rows.append(f"| {metric.replace('_', ' ').title()} | {'N/A' if value is None else f'{value:.4f}'} | {scored}/{payload['case_count']} |")
    rows.extend(["", "## Per-question scores", "", "| Case | Faithfulness | Answer relevance | Context precision | Sources |", "|---|---:|---:|---:|---:|"])
    for row in payload["cases"]:
        values = [row["scores"].get(key) for key in ("faithfulness", "answer_relevance", "context_precision")]
        formatted = [f"{value:.4f}" if isinstance(value, (int, float)) else "N/A" for value in values]
        rows.append(f"| {row['case_id']} | {formatted[0]} | {formatted[1]} | {formatted[2]} | {row['source_count']} |")
    rows.extend(["", "## Limitations", ""] + [f"- {item}" for item in payload["limitations"]])
    rows.extend(["", "Raw answers, retrieved title/abstract contexts, references, per-case scores, and metadata are in [`latest.json`](latest.json).", ""])
    return "\n".join(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", help="Litera API base URL (defaults to LITERA_API_URL or the production API).")
    parser.add_argument("--judge-model", help="Gemini judge model (defaults to RAGAS_JUDGE_MODEL or gemini-2.5-flash).")
    parser.add_argument("--limit", type=int, default=3, help="Number of benchmark queries; defaults to 3 to respect the hosted API's daily limit. Use 0 for all cases.")
    parser.add_argument("--timeout", type=int, default=600, help="Max seconds to wait for each Litera API job (default: 600).")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(evaluate(parse_args())))
    except Exception as exc:
        raise SystemExit(f"Evaluation could not complete: {exc}") from exc

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import Any, TypedDict

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()
app = FastAPI(title="Litera Research API", version="1.0.0", description="Inspectable multi-agent literature review workflow")
origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class QueryRequest(BaseModel):
    question: str = Field(min_length=12, max_length=1000)

class Job(TypedDict, total=False):
    id: str
    question: str
    status: str
    stage: str
    progress: int
    trace: list[dict[str, Any]]
    report: dict[str, Any]
    error: str
    started_at: float

JOBS: dict[str, Job] = {}

def trace(job: Job, name: str, detail: str, status: str = "completed") -> None:
    job.setdefault("trace", []).append({"step": name, "detail": detail, "status": status, "timestamp": int(time.time())})

def json_from_text(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The language model returned an invalid structured response.")
    return json.loads(cleaned[start:end + 1])

async def llm(prompt: str, *, json_mode: bool = False) -> str:
    """Provider adapter: selected provider first, then configured free-tier fallbacks."""
    preferred = os.getenv("LLM_PROVIDER", "gemini").lower()
    providers = {
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
        "groq": bool(os.getenv("GROQ_API_KEY")),
        "ollama": preferred == "ollama" or os.getenv("OLLAMA_ENABLED", "false").lower() == "true",
    }
    order = [preferred] + [name for name in ("gemini", "groq", "ollama") if name != preferred]
    configured = [name for name in order if providers.get(name)]
    if not configured:
        raise RuntimeError("Configure GEMINI_API_KEY or GROQ_API_KEY in backend/.env, or run Ollama locally. The sample review works without credentials.")
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for provider in configured:
            try:
                if provider == "gemini":
                    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                    body: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
                    if json_mode:
                        body["generationConfig"]["responseMimeType"] = "application/json"
                    response = await client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY']}", json=body)
                    if response.status_code == 429:
                        errors.append("Gemini free-tier limit reached")
                        continue
                    response.raise_for_status()
                    return response.json()["candidates"][0]["content"]["parts"][0]["text"]
                if provider == "groq":
                    body = {"model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
                    if json_mode:
                        body["response_format"] = {"type": "json_object"}
                    response = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
                    if response.status_code == 429:
                        errors.append("Groq free-tier limit reached")
                        continue
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"]
                if provider == "ollama":
                    body = {"model": os.getenv("OLLAMA_MODEL", "llama3.1:8b"), "prompt": prompt, "stream": False, "format": "json" if json_mode else "", "options": {"temperature": 0.2}}
                    response = await client.post(f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/generate", json=body)
                    response.raise_for_status()
                    return response.json()["response"]
            except (httpx.HTTPError, KeyError, IndexError) as exc:
                errors.append(f"{provider}: {exc.__class__.__name__}")
    raise RuntimeError("No configured language model completed the request (" + "; ".join(errors) + "). Check provider status and retry.")

async def planner(state: dict[str, Any]) -> dict[str, Any]:
    prompt = f"Decompose this research question into 3-4 concise, distinct literature search angles. Return JSON only as {{\"angles\":[\"...\"]}}. Question: {state['question']}"
    try:
        answer = json_from_text(await llm(prompt, json_mode=True))
        angles = [str(x)[:180] for x in answer.get("angles", [])[:4] if str(x).strip()]
        if len(angles) < 2:
            raise ValueError("Planner returned too few angles")
    except (ValueError, KeyError, json.JSONDecodeError):
        angles = [state["question"], f"Evidence and findings about {state['question']}", f"Limitations and research gaps in {state['question']}"]
    state["angles"] = angles
    state["job"]["progress"] = 15
    state["job"]["stage"] = "Searching academic literature"
    trace(state["job"], "Question planning", f"Question decomposed into {len(angles)} research angles")
    return state

async def semantic_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    headers = {"User-Agent": "LiteraResearchAssistant/1.0"}
    if os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    params = {"query": query, "limit": limit, "fields": "title,authors,year,abstract,venue,url,externalIds,openAccessPdf"}
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        if response.status_code == 429:
            await asyncio.sleep(2)
            response = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("data", [])

async def researcher(state: dict[str, Any]) -> dict[str, Any]:
    papers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for angle in state["angles"]:
        try:
            results = await semantic_search(angle, limit=6)
        except httpx.HTTPError as exc:
            state["job"]["error"] = f"Academic search failed: {exc.__class__.__name__}. Please retry shortly."
            raise RuntimeError(state["job"]["error"]) from exc
        for p in results:
            identity = p.get("paperId") or p.get("url") or p.get("title")
            if identity and identity not in seen:
                seen.add(identity)
                papers.append(p)
    if not papers:
        raise RuntimeError("Academic search returned no papers. Try a broader research question.")
    state["papers"] = papers[:20]
    state["job"]["progress"] = 42
    state["job"]["stage"] = "Indexing source passages"
    trace(state["job"], "Academic search", f"Semantic Scholar returned {len(papers)} unique papers")
    return state

def vector_context(papers: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    passages = [p for p in papers if p.get("abstract")]
    if not passages:
        return papers[:8]
    try:
        import chromadb
        client = chromadb.Client()
        collection = client.create_collection(name=f"review_{uuid.uuid4().hex[:12]}")
        docs = [f"{p.get('title','')}\n{p.get('abstract','')}" for p in passages]
        collection.add(ids=[str(i) for i in range(len(docs))], documents=docs, metadatas=[{"paper_id": str(i)} for i in range(len(docs))])
        result = collection.query(query_texts=[question], n_results=min(12, len(docs)))
        indices = [int(x) for x in result["ids"][0]]
        collection.delete(ids=[str(i) for i in range(len(docs))])
        return [passages[i] for i in indices]
    except (ImportError, ValueError, RuntimeError):
        return passages[:12]

def source_record(p: dict[str, Any], n: int) -> dict[str, Any]:
    ids = p.get("externalIds") or {}
    doi = ids.get("DOI")
    return {"id": n, "title": p.get("title") or "Untitled paper", "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:4]), "year": p.get("year"), "venue": p.get("venue"), "abstract": p.get("abstract"), "doi": doi, "url": f"https://doi.org/{doi}" if doi else p.get("url"), "pdf": (p.get("openAccessPdf") or {}).get("url")}

async def analyst(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("feedback"):
        state["revision_used"] = True
    context = vector_context(state["papers"], state["question"])
    state["job"]["progress"] = 58
    state["job"]["stage"] = "Synthesizing evidence"
    trace(state["job"], "Source analysis", f"{len(context)} source passages retrieved for synthesis")
    citations = [source_record(p, i + 1) for i, p in enumerate(context)]
    evidence = "\n\n".join(f"[{i+1}] {p.get('title')} ({p.get('year') or 'n.d.'}, {p.get('venue') or 'venue not listed'}). {p.get('abstract') or ''}" for i, p in enumerate(context))
    feedback = state.get("feedback") or "None on first draft."
    prompt = f"Write a rigorous first-pass literature review in Markdown for the research question below, using only the supplied papers. Organize it into 3-5 descriptive sections. Cite factual claims inline using [1], [2] corresponding to the numbered sources. Identify agreement and contradictions and distinguish correlation from causation. Never invent facts or references. If evidence is insufficient, state that. Incorporate this critic feedback if present: {feedback}\n\nQuestion: {state['question']}\n\nSources:\n{evidence}"
    state["draft"] = await llm(prompt)
    state["citations"] = citations
    return state

async def critic(state: dict[str, Any]) -> dict[str, Any]:
    state["job"]["progress"] = 76
    state["job"]["stage"] = "Critic review"
    prompt = f"Review this literature review. Return JSON only: {{\"revise\": true/false, \"feedback\": \"specific short actionable feedback\"}}. Flag missing source citations, unsupported assertions, or question aspects not covered. Ask for revision only for material issues. Question: {state['question']}\nSources available: {len(state['citations'])}\nDraft:\n{state['draft']}"
    try:
        result = json_from_text(await llm(prompt, json_mode=True))
        state["critic"] = result
    except (ValueError, KeyError, json.JSONDecodeError):
        state["critic"] = {"revise": False, "feedback": "Critic response could not be parsed; review source links manually."}
    if state["critic"].get("revise") and not state.get("revision_used"):
        state["feedback"] = str(state["critic"].get("feedback", "Add citations for key claims."))[:1200]
        state["revision_requested"] = True
        state["job"]["progress"] = 82
        trace(state["job"], "Critic review", "Actionable feedback found; one bounded revision requested")
    else:
        state["job"]["progress"] = 92
        trace(state["job"], "Critic review", "Draft checked; no further revision requested")
    return state

def route_critic(state: dict[str, Any]) -> str:
    return "revise" if state.get("revision_requested") and not state.get("revision_used") else "finish"

def build_graph():
    from langgraph.graph import END, StateGraph
    class ReviewState(TypedDict, total=False):
        question: str; angles: list[str]; papers: list[dict[str, Any]]; draft: str; citations: list[dict[str, Any]]; critic: dict[str, Any]; feedback: str; revision_used: bool; revision_requested: bool; job: Job
    graph = StateGraph(ReviewState)
    graph.add_node("planner", planner)
    graph.add_node("researcher", researcher)
    graph.add_node("analyst", analyst)
    graph.add_node("critic", critic)
    graph.set_entry_point("planner")
    graph.add_edge("planner", "researcher")
    graph.add_edge("researcher", "analyst")
    graph.add_edge("analyst", "critic")
    graph.add_conditional_edges("critic", route_critic, {"revise": "analyst", "finish": END})
    return graph.compile()

async def run_job(job_id: str) -> None:
    job = JOBS[job_id]
    job["status"] = "running"
    job["stage"] = "Planning research"
    try:
        graph = build_graph()
        result = await graph.ainvoke({"question": job["question"], "job": job, "revision_used": False})
        report = {"question": job["question"], "angles": result.get("angles", []), "review": result.get("draft", ""), "sources": result.get("citations", []), "critic": result.get("critic", {}), "generated_at": int(time.time())}
        job["report"] = report
        job["progress"] = 100
        job["stage"] = "Research complete"
        job["status"] = "completed"
        trace(job, "Review complete", f"Review generated with {len(report['sources'])} cited sources")
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)[:500]
        job["stage"] = "Research stopped"
        trace(job, "Research stopped", job["error"], "failed")

@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "Litera Research API", "status": "ok", "docs": "/docs"}

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "litera-api"}

@app.post("/query", status_code=202)
async def submit_query(payload: QueryRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"id": job_id, "question": payload.question.strip(), "status": "queued", "stage": "Queued", "progress": 0, "trace": [], "started_at": time.time()}
    background_tasks.add_task(run_job, job_id)
    return {"job_id": job_id, "status": "queued"}

@app.get("/status/{job_id}")
async def get_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found. Jobs are held in memory and may be lost when the API restarts.")
    return {k: job.get(k) for k in ("id", "question", "status", "stage", "progress", "trace", "error")}

@app.get("/report/{job_id}")
async def get_report(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Research job not found.")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="This research run has not completed.")
    return job["report"]

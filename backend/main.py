from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
import uuid
from typing import Any, TypedDict

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger("litera.research")
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
    user_id: str

JOBS: dict[str, Job] = {}
ACADEMIC_CACHE: dict[str, tuple[float, list[dict[str, Any]], str]] = {}
CROSSREF_REQUEST_LOCK = asyncio.Semaphore(1)
bearer = HTTPBearer(auto_error=False)

def auth_required() -> bool:
    return os.getenv("REQUIRE_AUTH", "false").lower() == "true"

async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    auth_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not auth_url or not anon_key:
        if auth_required():
            raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.")
        return "anonymous"
    if not credentials:
        if auth_required():
            raise HTTPException(status_code=401, detail="Sign in to start a literature review.")
        return "anonymous"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{auth_url}/auth/v1/user", headers={"apikey": anon_key, "Authorization": f"Bearer {credentials.credentials}"})
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="Your session has expired. Sign in again.")
        response.raise_for_status()
        user_id = response.json().get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Your session could not be verified.")
        return str(user_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable.") from exc

def owned_job(job_id: str, user_id: str) -> Job:
    job = JOBS.get(job_id)
    if not job or job.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Research run not found.")
    return job

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
                    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
                    body: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
                    if json_mode:
                        body["generationConfig"]["responseMimeType"] = "application/json"
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY']}"
                    response = None
                    for attempt in range(3):
                        try:
                            response = await client.post(url, json=body)
                        except (httpx.TimeoutException, httpx.ConnectError):
                            if attempt < 2:
                                await asyncio.sleep(2 ** attempt)
                                continue
                            raise
                        if response.status_code == 429:
                            errors.append("Gemini rate limit reached (HTTP 429)")
                            break
                        if response.status_code in {500, 502, 503, 504} and attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        response.raise_for_status()
                        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
                    if response is not None and response.status_code == 429:
                        continue
                if provider == "groq":
                    body = {"model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
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
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    detail = exc.response.text[:240]
                    api_key = os.getenv("GEMINI_API_KEY", "")
                    if api_key:
                        detail = detail.replace(api_key, "[redacted]")
                    errors.append(f"{provider}: HTTP {status}: {detail}")
                else:
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
    state["job"]["stage"] = "Searching academic papers"
    trace(state["job"], "Question planning", f"Question decomposed into {len(angles)} research angles")
    return state

async def semantic_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    headers = {"User-Agent": "LiteraResearchAssistant/1.0"}
    if os.getenv("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]
    normalized_query = re.sub(r"[\u2010-\u2015-]+", " ", query).strip()
    params = {"query": normalized_query, "limit": limit, "fields": "title,authors,year,abstract,venue,url,externalIds,openAccessPdf"}
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "2")
            try:
                delay = min(3.0, max(0.5, float(retry_after)))
            except ValueError:
                delay = 2.0
            await asyncio.sleep(delay)
            response = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("data", [])

def openalex_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    if not inverted:
        return None
    words: list[str] = []
    for word, positions in inverted.items():
        for position in positions:
            if position >= len(words):
                words.extend([""] * (position + 1 - len(words)))
            words[position] = word
    abstract = " ".join(word for word in words if word)
    return abstract or None

async def openalex_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Search OpenAlex when Semantic Scholar is unavailable or has no matches."""
    params: dict[str, Any] = {
        "search": re.sub(r"[\u2010-\u2015-]+", " ", query).strip(),
        "per-page": min(limit, 25),
        "select": "id,doi,title,publication_year,authorships,primary_location,open_access,abstract_inverted_index",
    }
    api_key = os.getenv("OPENALEX_API_KEY")
    if api_key:
        params["api_key"] = api_key
    async with httpx.AsyncClient(timeout=25, headers={"User-Agent": "LiteraResearchAssistant/1.0"}) as client:
        response = await client.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()
        works = response.json().get("results", [])
    papers: list[dict[str, Any]] = []
    for work in works:
        authors = [
            {"name": (entry.get("author") or {}).get("display_name", "")}
            for entry in work.get("authorships", [])[:8]
        ]
        location = work.get("primary_location") or {}
        venue = (location.get("source") or {}).get("display_name")
        open_access = work.get("open_access") or {}
        pdf_url = (location.get("pdf_url") or open_access.get("oa_url"))
        doi = work.get("doi")
        papers.append({
            "paperId": work.get("id"),
            "title": work.get("title"),
            "authors": authors,
            "year": work.get("publication_year"),
            "abstract": openalex_abstract(work.get("abstract_inverted_index")),
            "venue": venue,
            "url": doi or work.get("id"),
            "externalIds": {"DOI": doi.removeprefix("https://doi.org/")} if doi else {},
            "openAccessPdf": {"url": pdf_url} if pdf_url else None,
        })
    return papers

async def academic_search(query: str, limit: int = 6) -> tuple[list[dict[str, Any]], str]:
    """Use two independent indexes with a short cache to reduce rate-limit pressure."""
    cache_key = re.sub(r"\s+", " ", query).strip().casefold()
    cached = ACADEMIC_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 900:
        return cached[1][:limit], cached[2]
    if cached:
        ACADEMIC_CACHE.pop(cache_key, None)
    errors: list[str] = []
    try:
        papers = await semantic_search(query, limit=limit)
        if papers:
            ACADEMIC_CACHE[cache_key] = (time.time(), papers, "Semantic Scholar")
            return papers, "Semantic Scholar"
        logger.info("Semantic Scholar returned no matches; trying OpenAlex")
    except httpx.HTTPStatusError as exc:
        errors.append(f"Semantic Scholar HTTP {exc.response.status_code}")
        logger.warning("Semantic Scholar returned HTTP %s; trying OpenAlex", exc.response.status_code)
    except httpx.HTTPError as exc:
        errors.append(f"Semantic Scholar {exc.__class__.__name__}")
        logger.warning("Semantic Scholar request failed (%s); trying OpenAlex", exc.__class__.__name__)
    try:
        papers = await openalex_search(query, limit=limit)
        if papers:
            ACADEMIC_CACHE[cache_key] = (time.time(), papers, "OpenAlex")
            return papers, "OpenAlex"
        return [], "OpenAlex"
    except httpx.HTTPStatusError as exc:
        errors.append(f"OpenAlex HTTP {exc.response.status_code}")
        logger.warning("OpenAlex returned HTTP %s", exc.response.status_code)
    except httpx.HTTPError as exc:
        errors.append(f"OpenAlex {exc.__class__.__name__}")
        logger.warning("OpenAlex request failed (%s)", exc.__class__.__name__)
    raise RuntimeError("Primary academic indexes unavailable: " + "; ".join(errors))

def crossref_abstract(value: str | None) -> str | None:
    if not value:
        return None
    text = html.unescape(re.sub(r"<[^>]*>", " ", value))
    return re.sub(r"\s+", " ", text).strip() or None

async def crossref_search(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Last-resort scholarly search through Crossref's public metadata API."""
    cache_key = "crossref:" + re.sub(r"\s+", " ", query).strip().casefold()
    cached = ACADEMIC_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 900:
        return cached[1][:limit]
    params: dict[str, Any] = {
        "query.bibliographic": query[:500],
        "rows": min(limit, 20),
        "select": "DOI,title,author,published,container-title,abstract,URL,type,link",
    }
    mailto = os.getenv("CROSSREF_MAILTO")
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": "LiteraResearchAssistant/1.0"}
    if mailto:
        headers["User-Agent"] += f" (mailto:{mailto})"
    async with CROSSREF_REQUEST_LOCK:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get("https://api.crossref.org/works", params=params, headers=headers)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "2")
                try:
                    delay = min(5.0, max(1.0, float(retry_after)))
                except ValueError:
                    delay = 2.0
                await asyncio.sleep(delay)
                response = await client.get("https://api.crossref.org/works", params=params, headers=headers)
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
    papers: list[dict[str, Any]] = []
    for item in items:
        title = next(iter(item.get("title") or []), None)
        doi = item.get("DOI")
        if not title or not doi:
            continue
        author_names = []
        for author in item.get("author") or []:
            name = " ".join(part for part in (author.get("given"), author.get("family")) if part)
            if name:
                author_names.append({"name": name})
        date_parts = ((item.get("published") or {}).get("date-parts") or [[None]])[0]
        year = date_parts[0] if date_parts else None
        links = item.get("link") or []
        pdf_url = next((link.get("URL") for link in links if "pdf" in (link.get("content-type") or "").lower()), None)
        papers.append({
            "paperId": doi,
            "title": title,
            "authors": author_names,
            "year": year,
            "abstract": crossref_abstract(item.get("abstract")),
            "venue": next(iter(item.get("container-title") or []), None),
            "url": f"https://doi.org/{doi}",
            "externalIds": {"DOI": doi},
            "openAccessPdf": {"url": pdf_url} if pdf_url else None,
        })
    if papers:
        ACADEMIC_CACHE[cache_key] = (time.time(), papers, "Crossref")
    return papers

async def researcher(state: dict[str, Any]) -> dict[str, Any]:
    papers: list[dict[str, Any]] = []
    seen: set[str] = set()
    providers: set[str] = set()
    search_errors: list[str] = []
    for angle in state["angles"][:3]:
        try:
            results, provider = await academic_search(angle, limit=6)
        except RuntimeError as exc:
            search_errors.append(str(exc))
            # A failure from both primary indexes is a signal to use the single
            # broad Crossref query below instead of sending more rate-limited calls.
            break
        providers.add(provider)
        for p in results:
            identity = p.get("paperId") or p.get("url") or p.get("title")
            if identity and identity not in seen:
                seen.add(identity)
                papers.append(p)
    # Crossref is deliberately queried once per review, avoiding a burst of requests
    # from Render's shared free-tier egress IP when the primary providers are limited.
    if search_errors or len(papers) < 6:
        try:
            results = await crossref_search(state["question"], limit=18)
            for p in results:
                identity = p.get("paperId") or p.get("url") or p.get("title")
                if identity and identity not in seen:
                    seen.add(identity)
                    papers.append(p)
            if results:
                providers.add("Crossref")
                now = time.time()
                for search_term in [state["question"], *state["angles"][:3]]:
                    key = re.sub(r"\s+", " ", search_term).strip().casefold()
                    ACADEMIC_CACHE[key] = (now, results, "Crossref")
        except httpx.HTTPStatusError as exc:
            search_errors.append(f"Crossref HTTP {exc.response.status_code}")
            logger.warning("Crossref returned HTTP %s", exc.response.status_code)
        except httpx.HTTPError as exc:
            search_errors.append(f"Crossref {exc.__class__.__name__}")
            logger.warning("Crossref request failed (%s)", exc.__class__.__name__)
    if not papers:
        if search_errors:
            detail = "; ".join(search_errors)
            raise RuntimeError(f"No scholarly index returned usable papers ({detail}). Please try again shortly or use the sample review.")
        raise RuntimeError("Academic search returned no papers. Try a broader research question.")
    state["papers"] = papers[:20]
    state["job"]["progress"] = 42
    state["job"]["stage"] = "Selecting relevant studies"
    trace(state["job"], "Academic search", f"{', '.join(sorted(providers))} returned {len(papers)} unique papers")
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
    state["job"]["stage"] = "Writing your review"
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
    state["job"]["stage"] = "Checking source citations"
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
    job["stage"] = "Finding key research themes"
    try:
        graph = build_graph()
        result = await graph.ainvoke({"question": job["question"], "job": job, "revision_used": False})
        report = {"question": job["question"], "angles": result.get("angles", []), "review": result.get("draft", ""), "sources": result.get("citations", []), "critic": result.get("critic", {}), "generated_at": int(time.time())}
        job["report"] = report
        job["progress"] = 100
        job["stage"] = "Review ready"
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
    return {"status": "healthy", "service": "litera-api", "revision": os.getenv("RENDER_GIT_COMMIT", "local"), "auth_required": str(auth_required()).lower(), "auth_configured": str(bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY"))).lower(), "model_configured": str(bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY") or os.getenv("LLM_PROVIDER") == "ollama")).lower()}

@app.post("/query", status_code=202)
async def submit_query(payload: QueryRequest, background_tasks: BackgroundTasks, user_id: str = Depends(current_user)) -> dict[str, str]:
    daily_limit = max(1, int(os.getenv("DAILY_RESEARCH_LIMIT", "3")))
    recent_runs = sum(
        1 for job in JOBS.values()
        if job.get("user_id") == user_id
        and job.get("started_at", 0) > time.time() - 86400
        and job.get("status") in {"queued", "running", "completed"}
    )
    if recent_runs >= daily_limit:
        raise HTTPException(status_code=429, detail=f"You've reached today's research limit of {daily_limit} reviews. Come back tomorrow.")
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"id": job_id, "question": payload.question.strip(), "status": "queued", "stage": "Finding research angles", "progress": 0, "trace": [], "started_at": time.time(), "user_id": user_id}
    background_tasks.add_task(run_job, job_id)
    return {"job_id": job_id, "status": "queued"}

@app.get("/status/{job_id}")
async def get_status(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    job = owned_job(job_id, user_id)
    return {k: job.get(k) for k in ("id", "question", "status", "stage", "progress", "trace", "error")}

@app.get("/report/{job_id}")
async def get_report(job_id: str, user_id: str = Depends(current_user)) -> dict[str, Any]:
    job = owned_job(job_id, user_id)
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="This research run has not completed.")
    return job["report"]

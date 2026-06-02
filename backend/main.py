from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import random
import re
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import pdfplumber
import spacy
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from docx import Document
from pydantic import BaseModel, validator

# ---------------------------------------------------------------------------
# FIX 1 — Prompt-injection sanitizer
# ---------------------------------------------------------------------------

_PROMPT_INJECTION_RE = re.compile(
    r'(ignore\s+(previous|all|prior)|disregard|system\s*prompt|you\s+are\s+now|'
    r'act\s+as|jailbreak|forget\s+(your|all)|new\s+instructions?)',
    re.IGNORECASE
)


def _sanitize_for_prompt(text: str, max_len: int = 500) -> str:
    """Strip prompt-injection attempts and truncate user-controlled text before LLM insertion."""
    if not text:
        return ""
    sanitized = _PROMPT_INJECTION_RE.sub("[removed]", text)
    return sanitized[:max_len]


# ---------------------------------------------------------------------------
# Directories & logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "parser.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("parser")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="CareerMaster AI Parser", version="1.0.0")

# CORS — restricted origins for production; keep "*" commented for dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev mode — restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request timing middleware — logs every request + duration
# ---------------------------------------------------------------------------

import time

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%-6s %-35s → %d  (%.0fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
    logger.debug(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ---------------------------------------------------------------------------
# spaCy model
# ---------------------------------------------------------------------------

try:
    nlp = spacy.load("en_core_web_sm")
    logger.info("spaCy model en_core_web_sm loaded")
except OSError:
    logger.warning("spaCy model missing, falling back to blank model")
    nlp = spacy.blank("en")

# ---------------------------------------------------------------------------
# Optional Groq client (for LLM-powered suggestions)
# ---------------------------------------------------------------------------

_groq_client = None
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if GROQ_API_KEY:
    try:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
        logger.info("Groq client initialised (model: llama-3.3-70b-versatile)")
    except Exception as exc:
        logger.warning("Could not initialise Groq client: %s", exc)
else:
    logger.info("GROQ_API_KEY not set — AI features will use smart template fallback")

# ---------------------------------------------------------------------------
# AI helpers — Groq LLM wrappers
# ---------------------------------------------------------------------------

_GROQ_MODEL = "llama-3.3-70b-versatile"          # free-tier, fast, high quality
_GROQ_MODEL_FAST = "llama-3.1-8b-instant"         # ultra-fast fallback


async def _groq_chat(system: str, user: str, max_tokens: int = 800, temperature: float = 0.7) -> Optional[str]:
    """Low-level Groq chat wrapper. Returns None on any failure."""
    if _groq_client is None:
        return None
    for model in (_GROQ_MODEL, _GROQ_MODEL_FAST):
        try:
            resp = await asyncio.to_thread(
                _groq_client.chat.completions.create,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if not resp.choices:
                logger.warning("Groq returned empty choices for model %s", model)
                continue
            if not resp.choices:
                logger.warning("Groq returned empty choices list for model %s", model)
                continue  # try next model in fallback
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("Groq call failed with model %s: %s", model, exc)
    return None


async def _ai_generate_questions(
    skills: List[str],
    role: str,
    difficulty: str,
    count: int,
    experience: Optional[List[Dict[str, str]]] = None,
    summary: str = "",
) -> Optional[List[Dict[str, Any]]]:
    """
    Use Groq LLM to generate highly personalised interview questions.
    Returns a list of question dicts on success, None on failure.
    """
    skills_str = ", ".join(skills[:MAX_SKILLS_IN_PROMPT]) if skills else "general software engineering"
    exp_str = ""
    if experience:
        exp_lines = [
            f"  - {_sanitize_for_prompt(e.get('title',''), 100)}"
            + (f" at {_sanitize_for_prompt(e.get('company',''), 100)}" if e.get('company') else "")
            + (f" ({e.get('dates','')})" if e.get('dates') else "")
            for e in experience[:MAX_EXPERIENCE_IN_PROMPT]
        ]
        exp_str = "\nWork experience:\n" + "\n".join(exp_lines)

    system = (
        "You are an expert technical recruiter and career coach. "
        "Generate highly personalised, varied interview questions for a candidate. "
        "Return ONLY a valid JSON array — no markdown fences, no extra text. "
        "Each element must be an object with these keys: "
        "\"text\" (string), \"category\" (one of: behavioral, technical, situational, role_specific), "
        "\"skill_focus\" (string), \"star_required\" (boolean), \"difficulty\" (string). "
        "Mix question types: ~40% behavioral, ~35% technical, ~15% situational, ~10% role-specific. "
        "For behavioral questions, require STAR answers. "
        "Make questions specific to the candidate's actual skills, not generic."
    )

    user = (
        f"Generate exactly {count} interview questions for:\n"
        f"Role: {role}\n"
        f"Seniority: {difficulty}\n"
        f"Key skills: {skills_str}"
        f"{exp_str}"
        + (f"\nCV summary: {_sanitize_for_prompt(summary, 300)}" if summary else "")
        + "\n\nReturn only the JSON array."
    )

    raw = await _groq_chat(system, user, max_tokens=2000, temperature=0.75)
    if not raw:
        return None

    # Extract JSON array from response (guard against any extra text)
    try:
        # Try to find a JSON array in the response
        json_match = re.search(r"\[[\s\S]*\]", raw)
        if not json_match:
            logger.warning("AI question response had no JSON array")
            return None
        items = json.loads(json_match.group(0))
        if not isinstance(items, list) or not items:
            return None

        questions: List[Dict[str, Any]] = []
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not item.get("text"):
                continue
            questions.append({
                "id": i,
                "text": str(item["text"]).strip(),
                "category": str(item.get("category", "behavioral")),
                "skill_focus": str(item.get("skill_focus", skills[0] if skills else role)),
                "difficulty": str(item.get("difficulty", difficulty)),
                "star_required": bool(item.get("star_required", item.get("category") == "behavioral")),
                "time_limit_seconds": 120,
                "ai_generated": True,
            })
        return questions if questions else None
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Could not parse AI question JSON: %s | raw=%s", exc, raw[:200])
        return None


async def _ai_analyze_answer(
    question_text: str,
    response_text: str,
    star_scores: Dict[str, int],
    total_score: float,
    grade: str,
) -> Optional[Dict[str, Any]]:
    """
    Use Groq LLM to enrich STAR evaluation with natural-language coaching feedback.
    Returns a dict with 'coaching_feedback', 'strengths_narrative', 'improvement_tips'.
    Returns None on failure.
    """
    star_summary = "; ".join(
        f"{k.upper()}={v}%" for k, v in star_scores.items()
    )

    system = (
        "You are a professional interview coach specialising in the STAR framework. "
        "Analyse the candidate's interview answer and provide structured coaching feedback. "
        "Be specific, constructive, and encouraging. "
        "Return ONLY valid JSON with exactly these keys: "
        "\"coaching_feedback\" (string, 2-3 sentences overall assessment), "
        "\"strengths_narrative\" (string, 1-2 sentences on what they did well), "
        "\"improvement_tips\" (array of 2-3 short actionable strings), "
        "\"example_improvement\" (string, a one-sentence example of how to improve the weakest part). "
        "No markdown, no extra keys."
    )

    user = (
        f"Interview question: {_sanitize_for_prompt(question_text, 300)}\n\n"
        f"Candidate answer: {_sanitize_for_prompt(response_text, 1000)}\n\n"
        f"STAR component scores (0-100): {star_summary}\n"
        f"Overall score: {total_score}/100 (Grade: {grade})\n\n"
        "Provide coaching feedback as JSON."
    )

    raw = await _groq_chat(system, user, max_tokens=500, temperature=0.6)
    if not raw:
        return None

    try:
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            return None
        data = json.loads(json_match.group(0))
        # Validate required keys
        required = {"coaching_feedback", "strengths_narrative", "improvement_tips", "example_improvement"}
        if not required.issubset(data.keys()):
            return None
        if not isinstance(data["improvement_tips"], list):
            data["improvement_tips"] = [str(data["improvement_tips"])]
        return data
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Could not parse AI analysis JSON: %s | raw=%s", exc, raw[:200])
        return None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ATS scoring constants
ATS_BASE_SCORE = 20
ATS_SKILL_POINTS = 5
ATS_SKILL_CAP = 30
ATS_METRICS_BONUS = 10
ATS_FORMAT_BONUS = 10
ATS_MAX_SCORE = 100
ATS_KEYWORD_COVERAGE_BASE = 60
ATS_KEYWORD_COVERAGE_CAP = 95

# STAR evaluation thresholds
STAR_HIT_EXCELLENT = 4
STAR_HIT_GOOD = 3
STAR_HIT_FAIR = 2
STAR_HIT_POOR = 1
STAR_SCORE_EXCELLENT = 100
STAR_SCORE_GOOD = 85
STAR_SCORE_FAIR = 70
STAR_SCORE_POOR = 45
STAR_SCORE_WEAK = 15

# Question generation
MAX_QUESTION_COUNT = 50
MAX_SKILLS_IN_PROMPT = 12
MAX_EXPERIENCE_IN_PROMPT = 4

# Answer evaluation
MIN_ANSWER_WORDS = 50
MAX_ANSWER_WORDS = 300

SKILL_KEYWORDS = {
    "python", "django", "react", "typescript", "javascript", "java",
    "c++", "c#", "c", "lua", "sql", "postgresql", "redis", "nlp", "api",
    "docker", "aws", "node.js", "mongodb", "flask", "bootstrap", "git",
    "matlab", "simulink", "html", "css", "pytest", "unit testing",
    "web speech api", "websocket", "machine learning", "data analysis",
    "spacy",
}

# Skills that contain regex-special chars need literal matching (not \b boundaries)
_SPECIAL_CHAR_SKILLS = {"c++", "c#", "node.js", "web speech api"}
# Skills that are single letters need strict whole-word matching
_SINGLE_CHAR_SKILLS = {"c"}

ROLE_PATTERNS = [
    (re.compile(r"data scientist|data analyst", re.I), "Data Scientist"),
    (re.compile(r"backend", re.I), "Backend Engineer"),
    (re.compile(r"front-end|frontend|ui/?ux", re.I), "Frontend Engineer"),
    (re.compile(r"software developer|software engineer", re.I), "Software Engineer"),
]

# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


def extract_text_pdf(content: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages)
    except Exception as exc:
        logger.error("PDF parsing failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Could not parse PDF: {exc}")


def extract_text_docx(content: bytes) -> str:
    try:
        doc = Document(io.BytesIO(content))
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as exc:
        logger.error("DOCX parsing failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Could not parse DOCX: {exc}")


# ---------------------------------------------------------------------------
# CV analysis helpers
# ---------------------------------------------------------------------------


def detect_contacts(text: str) -> Dict[str, str]:
    email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    phone_match = re.search(r"(\+?\d[\d\s().-]{8,20})", text)
    return {
        "email": email_match.group(0) if email_match else "\u2014",
        "phone": phone_match.group(0).strip() if phone_match else "\u2014",
    }


def detect_name(text: str) -> str:
    doc = nlp(text[:5000])
    person_entities = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    if person_entities:
        return person_entities[0].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:10]:
        if re.search(r"@|linkedin|github|portfolio|http", line, re.I):
            continue
        if re.search(r"\d", line):
            continue
        if len(line.split()) >= 2:
            return line.strip()
    return (lines[0].strip() if lines else "Candidate")


def detect_role(text: str) -> str:
    for pattern, role in ROLE_PATTERNS:
        if pattern.search(text):
            return role
    return "Software Engineer"


def extract_skills(text: str) -> List[str]:
    lower = text.lower()
    detected: List[str] = []
    for skill in SKILL_KEYWORDS:
        if skill in _SPECIAL_CHAR_SKILLS:
            # Literal substring match preceded/followed by non-alphanumeric or line boundary
            pattern = r'(?<![a-zA-Z0-9])' + re.escape(skill) + r'(?![a-zA-Z0-9])'
        else:
            # Standard word-boundary match
            pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, lower, re.IGNORECASE):
            detected.append(skill)
    return sorted(set(detected))


def estimate_metrics(text: str, skills: List[str] = None) -> Dict[str, Any]:
    if skills is None:
        skills = extract_skills(text)
    metrics_count = len(re.findall(r"\d+%|\d+\s?(ms|sec|seconds|users|x|times)", text, re.I))
    if not skills:
        keyword_coverage = 0
    else:
        keyword_coverage = min(ATS_KEYWORD_COVERAGE_CAP, ATS_KEYWORD_COVERAGE_BASE + len(skills) * ATS_SKILL_POINTS)
    action_verbs = len(
        re.findall(
            r"\b(built|led|optimized|designed|delivered|implemented|created|improved)\b",
            text, re.I,
        )
    )
    education_hits = len(re.findall(r"bsc|msc|university|college|degree", text, re.I))
    return {
        "metrics_count": metrics_count,
        "keyword_coverage": keyword_coverage,
        "action_verbs": action_verbs,
        "education_hits": education_hits,
    }


# Pre-compiled education regex patterns (module level)
_edu_pattern = re.compile(
    r"\b(bsc|msc|ba|ma|phd|bachelor|master|university|college|degree|"
    r"institute of technology|polytechnic|school of)\b",
    re.IGNORECASE,
)
_skip_pattern = re.compile(r"@|http|linkedin|github|skills?:", re.IGNORECASE)


def extract_education(text: str) -> List[str]:
    education_lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _skip_pattern.search(line):
            continue
        if _edu_pattern.search(line):
            education_lines.append(line)
    # Deduplicate while preserving order; drop lines that are substrings of others
    seen: List[str] = []
    for item in dict.fromkeys(education_lines):
        if not any(item in other and item != other for other in education_lines):
            seen.append(item)
    return seen[:6]


def extract_experience(text: str) -> List[Dict[str, str]]:
    """Extract job titles, companies, and date ranges from CV text."""
    experiences: List[Dict[str, str]] = []

    # Common job-title patterns
    title_pattern = re.compile(
        r"(?P<title>"
        r"(?:senior\s+|junior\s+|lead\s+|principal\s+|staff\s+)?"
        r"(?:software\s+engineer|software\s+developer|data\s+scientist|data\s+analyst|"
        r"backend\s+(?:engineer|developer)|frontend\s+(?:engineer|developer)|"
        r"full[\s-]?stack\s+(?:engineer|developer)|devops\s+engineer|"
        r"machine\s+learning\s+engineer|product\s+manager|project\s+manager|"
        r"qa\s+engineer|test\s+engineer|ui/?ux\s+designer|web\s+developer|"
        r"systems?\s+engineer|cloud\s+engineer|site\s+reliability\s+engineer|"
        r"research\s+(?:assistant|engineer|scientist)|intern(?:ship)?|"
        r"teaching\s+assistant)"
        r")",
        re.IGNORECASE,
    )

    # Date range: "Jan 2020 - Mar 2022", "2019-2021", "06/2020 - Present"
    date_pattern = re.compile(
        r"(?P<dates>"
        r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?\d{4}"
        r"\s*[-\u2013\u2014]\s*"
        r"(?:(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+)?\d{4}|[Pp]resent|[Cc]urrent)"
        r")",
        re.IGNORECASE,
    )

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        title_match = title_pattern.search(line)
        if not title_match:
            continue

        entry: Dict[str, str] = {"title": title_match.group("title").strip()}

        # Look for a date in this line or the next couple of lines
        window = " ".join(lines[max(0, idx - 1): idx + 3])
        date_match = date_pattern.search(window)
        entry["dates"] = date_match.group("dates").strip() if date_match else ""

        # Try to find company via spaCy ORG entities in same window.
        # Strip trailing date noise (e.g. "Google 2020-2023" -> "Google").
        doc = nlp(window[:500])
        orgs = [ent.text for ent in doc.ents if ent.label_ == "ORG"]
        company_found = ""
        for org_text in orgs:
            raw_company = org_text
            # Remove trailing year / date range that spaCy may have included
            raw_company = re.sub(
                r'\s*[\d]{4}[\s\-–—]*(?:[\d]{4}|[Pp]resent|[Cc]urrent)?$',
                '', raw_company,
            ).strip()
            # Skip if it looks like an email address or a bare phone fragment
            if "@" in raw_company:
                continue
            if re.fullmatch(r'[\d\s()+.-]{3,}', raw_company):
                continue
            # Skip very short strings (likely noise)
            if len(raw_company) < 2:
                continue
            company_found = raw_company
            break
        entry["company"] = company_found

        experiences.append(entry)

    return experiences


# ---------------------------------------------------------------------------
# ATS scoring — improved formula
# ---------------------------------------------------------------------------

# Pre-compiled ATS section heading regex (module level)
_ATS_SECTION_HEADING_RE = re.compile(r"^[A-Z][A-Z\s]{2,}$", re.MULTILINE)


def ats_score(text: str, metrics: Dict[str, Any], skills: List[str]) -> int:
    """
    New formula: base 20, +5 per skill (max 30), +10 if metrics present,
    +10 if action verbs present, +10 if education, +10 if experience section,
    +10 if well-formatted.  Allows scores below 50 for weak CVs.
    """
    score = ATS_BASE_SCORE

    # Skills contribution: +5 per skill, capped at 30
    score += min(ATS_SKILL_CAP, len(skills) * ATS_SKILL_POINTS)

    # Metrics present
    if metrics.get("metrics_count", 0) > 0:
        score += ATS_METRICS_BONUS

    # Action verbs present
    if metrics.get("action_verbs", 0) > 0:
        score += ATS_FORMAT_BONUS

    # Education section found
    if metrics.get("education_hits", 0) > 0:
        score += ATS_FORMAT_BONUS

    # Experience section present
    if re.search(r"\b(experience|work\s+history|employment)\b", text, re.IGNORECASE):
        score += ATS_FORMAT_BONUS

    # Well-formatted (has clear section headings)
    section_headings = _ATS_SECTION_HEADING_RE.findall(text)
    if len(section_headings) >= 2:
        score += ATS_FORMAT_BONUS

    return min(ATS_MAX_SCORE, score)


# ---------------------------------------------------------------------------
# FIX 2 — TTL cleanup for session files
# ---------------------------------------------------------------------------


def _cleanup_old_sessions():
    """Delete session files older than 24 hours."""
    cutoff = datetime.utcnow().timestamp() - 86400
    try:
        for f in OUTPUT_DIR.glob("parsed_*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


# ============================================================================
# GET /health
# ============================================================================


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": "1.0.0",
        "ai_enabled": _groq_client is not None,
        # FIX 4: model name omitted — exposes internal configuration details
    }


@app.get("/ai/status")
async def ai_status():
    """Check AI integration status and which model is active."""
    return {
        "groq_configured": _groq_client is not None,
        "model": "large language model" if _groq_client else None,  # FIX 4: redact exact model name
        "fallback_model": "large language model (fast)" if _groq_client else None,  # FIX 4: redact exact model name
        "features": {
            "ai_question_generation": _groq_client is not None,
            "ai_answer_analysis": _groq_client is not None,
            "ai_live_suggestions": _groq_client is not None,
        },
        "setup_instructions": (
            None if _groq_client else
            "Get a free API key at https://console.groq.com (no credit card required). "
            "Then set the GROQ_API_KEY environment variable and restart the server."
        ),
    }



async def _write_session(path, data):
    """Write session JSON to disk off the event loop to avoid blocking."""
    def _write():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Failed to persist session to %s: %s", path, e)
    await asyncio.to_thread(_write)

# ============================================================================
# POST /parse — CV parsing
# ============================================================================


@app.post("/parse")
async def parse_cv(request: Request, file: UploadFile = File(...)) -> Dict[str, Any]:
    _cleanup_old_sessions()  # remove stale PII files on each upload

    # Check Content-Length header first to reject oversized uploads before full read
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 10_000_000:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    content = await file.read()

    # Secondary size check on actual bytes received
    if len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10 MB.")

    filename = file.filename or ""
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    if ext not in ("pdf", "docx"):
        raise HTTPException(status_code=415, detail=f"Unsupported file type. Upload PDF or DOCX.")
    logger.info("Upload received: %s (%s)", filename, ext)

    if ext == "pdf":
        logger.info("Parsing PDF via pdfplumber")
        text = extract_text_pdf(content)
    elif ext == "docx":
        logger.info("Parsing DOCX via python-docx")
        text = extract_text_docx(content)

    if not text.strip():
        raise HTTPException(
            status_code=400,
            detail="Could not extract any text from the file. "
                   "If this is a scanned PDF (image-based), please convert it to a text-based PDF "
                   "or use a DOCX version of your CV."
        )

    contacts = detect_contacts(text)
    name = detect_name(text)
    role = detect_role(text)
    skills = extract_skills(text)
    metrics = estimate_metrics(text, skills=skills)
    education = extract_education(text)
    experience = extract_experience(text)
    score = ats_score(text, metrics, skills)

    payload = {
        "name": name,
        "role": role,
        "email": contacts["email"],
        "phone": contacts["phone"],
        "skills": skills,
        "filename": file.filename,
        "ats_score": score,
        "summary": text[:500],
        "metrics_count": metrics["metrics_count"],
        "education": education,
        "experience": experience,
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_id = uuid.uuid4().hex[:8]  # non-enumerable filename
    output_path = OUTPUT_DIR / f"parsed_{timestamp}_{short_id}.json"
    await _write_session(output_path, payload)
    logger.info("Parsed payload stored: %s", output_path)

    return payload


# ============================================================================
# FR-04: Mock Interview Question Generation
# ============================================================================

QUESTION_TEMPLATES = {
    "behavioral": [
        "Tell me about a time when you {action} using {skill}. What was the outcome?",
        "Describe a situation where you had to {action} under pressure. How did you handle it?",
        "Give an example of when you demonstrated {skill} in a team setting.",
        "How have you used {skill} to solve a complex problem?",
        "Tell me about a project where {skill} was critical to success.",
    ],
    "technical": [
        "Explain how you would design a system that uses {skill}.",
        "What are the best practices for {skill} in production environments?",
        "How would you optimize a {skill}-based solution for scalability?",
        "Describe your experience with {skill} and any challenges you faced.",
        "Walk me through how you would debug an issue related to {skill}.",
    ],
    "situational": [
        "If you encountered a performance issue with {skill}, how would you approach it?",
        "How would you prioritize tasks if multiple {skill} projects had tight deadlines?",
        "What would you do if a {skill} implementation wasn't meeting requirements?",
        "How would you mentor a junior developer on {skill}?",
        "Describe how you stay updated with {skill} trends and best practices.",
    ],
}

ACTIONS = [
    "led a team", "optimized performance", "solved a critical bug",
    "implemented a feature", "improved reliability", "reduced costs",
    "automated a process", "collaborated cross-functionally",
]


class InterviewRequest(BaseModel):
    skills: Optional[List[str]] = None
    role: str = "Software Engineer"
    difficulty: str = "mid"
    count: int = 10
    # Optional CV context for richer AI generation
    experience: Optional[List[Dict[str, str]]] = None
    summary: Optional[str] = None

    @validator('count')
    def count_must_be_non_negative(cls, v):
        if v < 0:
            raise ValueError('count cannot be negative')
        return min(v, MAX_QUESTION_COUNT)


class EvaluateRequest(BaseModel):
    question_id: int = 1
    question_text: str = ""
    response_text: str


def _template_generate_questions(
    skills: List[str],
    role: str,
    difficulty: str,
    count: int,
) -> List[Dict[str, Any]]:
    """Template-based question generation (fallback when Groq is unavailable).

    Generates up to *count* unique questions spread evenly across behavioral,
    technical, and situational categories, plus one role-specific question
    if the role is recognised.  Avoids duplicate texts by exhausting all
    (template × skill) combinations before repeating.
    """
    if count <= 0:
        return []

    role_questions: Dict[str, str] = {
        "Software Engineer": "How do you ensure code quality and maintainability in a team environment?",
        "Data Scientist": "Explain your approach to feature engineering and model selection.",
        "Backend Engineer": "How do you design APIs for high availability and fault tolerance?",
        "Frontend Engineer": "How do you optimise frontend performance and improve user experience?",
    }

    # Reserve one slot for the role-specific question if applicable
    has_role_q = role in role_questions
    template_slots = max(0, count - (1 if has_role_q else 0))

    categories = list(QUESTION_TEMPLATES.items())
    num_cats = len(categories)
    base = template_slots // num_cats if num_cats else 0
    remainder = template_slots % num_cats if num_cats else 0

    questions: List[Dict[str, Any]] = []
    used_texts: set = set()
    question_id = 1

    for cat_idx, (category, templates) in enumerate(categories):
        # Last category absorbs any leftover slots
        cat_count = base + (remainder if cat_idx == num_cats - 1 else 0)
        if cat_count <= 0:
            continue

        # Build all (template, skill) pairs, shuffle for variety
        pairs = [(t, s) for t in templates for s in skills]
        random.shuffle(pairs)

        # Extend with repeats if needed (different random actions)
        while len(pairs) < cat_count:
            extra = [(t, s) for t in templates for s in skills]
            random.shuffle(extra)
            pairs.extend(extra)

        for tmpl, skill in pairs:
            if len([q for q in questions if q["category"] == category]) >= cat_count:
                break
            action = random.choice(ACTIONS)
            raw_text = tmpl.format(skill=skill, action=action)

            if difficulty == "senior":
                raw_text = raw_text.replace("Tell me about", "Describe in detail")
                if "Include metrics and business impact." not in raw_text:
                    raw_text += " Include metrics and business impact."
            elif difficulty == "junior":
                raw_text = raw_text.replace("complex", "simple")

            if raw_text in used_texts:
                continue
            used_texts.add(raw_text)

            questions.append({
                "id": question_id,
                "text": raw_text,
                "category": category,
                "skill_focus": skill,
                "difficulty": difficulty,
                "star_required": category == "behavioral",
                "time_limit_seconds": 120,
                "ai_generated": False,
            })
            question_id += 1

    # Add role-specific question if there is capacity
    if has_role_q and len(questions) < count:
        questions.append({
            "id": question_id,
            "text": role_questions[role],
            "category": "role_specific",
            "skill_focus": role,
            "difficulty": difficulty,
            "star_required": False,
            "time_limit_seconds": 120,
            "ai_generated": False,
        })

    return questions[:count]


@app.post("/interview/generate")
async def generate_questions(request: InterviewRequest) -> Dict[str, Any]:
    """
    Generate 10-15 personalised interview questions based on CV skills/experience.

    Strategy:
    1. Try Groq LLM (llama-3.3-70b-versatile) for highly personalised questions.
    2. Fall back to smart template-based generation if Groq is unavailable.
    """
    skills = request.skills or ["python", "problem solving", "communication"]
    role = request.role
    difficulty = request.difficulty
    # count is already validated and bounded by InterviewRequest.count_must_be_positive (FIX 5)
    count = request.count
    experience = request.experience or []
    summary = request.summary or ""

    source = "ai"

    # --- Attempt AI generation ---
    questions = await _ai_generate_questions(
        skills=skills,
        role=role,
        difficulty=difficulty,
        count=count,
        experience=experience,
        summary=summary,
    )

    # --- Fallback to template generation ---
    if not questions:
        source = "template"
        logger.info("Using template fallback for question generation (Groq unavailable or failed)")
        questions = _template_generate_questions(skills, role, difficulty, count)

    logger.info(
        "Generated %d interview questions for role=%s, source=%s",
        len(questions), role, source,
    )

    return {
        "role": role,
        "difficulty": difficulty,
        "total_questions": len(questions),
        "estimated_duration_minutes": len(questions) * 2,
        "source": source,
        "ai_powered": source == "ai",
        "questions": questions,
    }


# ============================================================================
# FR-05: STAR Framework Feedback Analysis — improved with spaCy
# ============================================================================

STAR_CRITERIA = {
    "situation": {
        "keywords": [
            "when", "while", "during", "at", "in my role", "context",
            "background", "scenario",
        ],
        "weight": 25,
        "description": "Context and background of the situation",
    },
    "task": {
        "keywords": [
            "responsible", "needed to", "had to", "goal", "objective",
            "assigned", "tasked", "challenge",
        ],
        "weight": 25,
        "description": "Your specific responsibility or task",
    },
    "action": {
        "keywords": [
            "i did", "i led", "i created", "i built", "i implemented",
            "i designed", "i developed", "i analyzed", "i collaborated",
        ],
        "weight": 30,
        "description": "Specific actions you took",
    },
    "result": {
        "keywords": [
            "resulted", "achieved", "improved", "reduced", "increased",
            "saved", "delivered", "outcome", "%", "percent",
        ],
        "weight": 20,
        "description": "Measurable outcomes and impact",
    },
}


STATE_VERBS = {"be", "am", "is", "are", "was", "were", "feel", "felt", "think", "thought",
               "know", "knew", "seem", "appear", "become", "have", "had"}


# Pre-compiled STAR regex patterns (module level — avoids recompiling on every call)
_STAR_SITUATION_RE = re.compile(
    r"\b(when|while|during|at the time|in \d{4}|last year|previously|"
    r"back when|at my|in my role|at .{2,30} company)\b",
    re.IGNORECASE,
)
_STAR_TASK_RE = re.compile(
    r"\b(responsible for|needed to|had to|my goal|my objective|"
    r"assigned to|tasked with|was asked to|the challenge was|"
    r"requirement was)\b",
    re.IGNORECASE,
)
_STAR_ACTION_RE = re.compile(
    r"\bI\s+(built|led|created|implemented|designed|developed|analyzed|"
    r"collaborated|wrote|configured|deployed|refactored|tested|"
    r"integrated|launched|managed|migrated|optimized|proposed|"
    r"researched|solved|trained|automated)\b",
    re.IGNORECASE,
)
_STAR_RESULT_RE = re.compile(
    r"\b(result(?:ed|ing)?|achiev(?:ed|ing)|improv(?:ed|ing)|"
    r"reduc(?:ed|ing)|increas(?:ed|ing)|sav(?:ed|ing)|deliver(?:ed|ing)|"
    r"outcome|led to|enabled|grew|boosted)\b|\d+%",
    re.IGNORECASE,
)


def _spacy_star_analysis(text: str) -> Dict[str, Dict[str, Any]]:
    """Use spaCy sentence segmentation and dependency parsing for deeper
    STAR component detection."""
    doc = nlp(text)
    sentences = list(doc.sents) if doc.has_annotation("SENT_START") else [doc]

    component_hits: Dict[str, List[str]] = {
        "situation": [],
        "task": [],
        "action": [],
        "result": [],
    }

    for sent in sentences:
        sent_text = sent.text
        if _STAR_SITUATION_RE.search(sent_text):
            component_hits["situation"].append(sent_text.strip())
        if _STAR_TASK_RE.search(sent_text):
            component_hits["task"].append(sent_text.strip())
        if _STAR_ACTION_RE.search(sent_text):
            component_hits["action"].append(sent_text.strip())
        if _STAR_RESULT_RE.search(sent_text):
            component_hits["result"].append(sent_text.strip())

    # Also check dependency parse: first-person subjects with action verb roots
    for sent in sentences:
        for token in sent:
            if (
                token.dep_ in ("nsubj", "nsubjpass")
                and token.text.lower() in ("i", "we")
                and token.head.pos_ == "VERB"
                and token.head.lemma_.lower() not in STATE_VERBS
            ):
                if sent.text.strip() not in component_hits["action"]:
                    component_hits["action"].append(sent.text.strip())

    return component_hits


@app.post("/interview/evaluate")
async def evaluate_response(request: EvaluateRequest) -> Dict[str, Any]:
    """Analyze response using STAR framework with spaCy NLP and provide feedback."""
    question_id = request.question_id
    question_text = request.question_text
    response_text = request.response_text

    if not response_text or not response_text.strip():
        raise HTTPException(status_code=400, detail="Answer text cannot be empty.")
    response_text = response_text.strip()

    response_lower = response_text.lower()
    word_count = len(response_text.split())

    # ---- spaCy-based deep analysis ----
    component_hits = _spacy_star_analysis(response_text)

    # ---- Keyword-based analysis (fallback / supplementary) ----
    star_scores: Dict[str, int] = {}
    star_feedback: Dict[str, str] = {}
    detailed_feedback: Dict[str, Dict[str, Any]] = {}
    total_score = 0.0

    for component, config in STAR_CRITERIA.items():
        keyword_matches = sum(1 for kw in config["keywords"] if kw in response_lower)
        nlp_matches = len(component_hits.get(component, []))
        combined = keyword_matches + nlp_matches

        if combined >= STAR_HIT_EXCELLENT:
            score = STAR_SCORE_EXCELLENT
            feedback = f"Excellent {component.upper()} \u2014 clearly described"
        elif combined >= STAR_HIT_GOOD:
            score = STAR_SCORE_GOOD
            feedback = f"Strong {component.upper()} \u2014 minor additions possible"
        elif combined >= STAR_HIT_FAIR:
            score = STAR_SCORE_FAIR
            feedback = f"Good {component.upper()} \u2014 could add more detail"
        elif combined >= STAR_HIT_POOR:
            score = STAR_SCORE_POOR
            feedback = f"Partial {component.upper()} \u2014 needs more context"
        else:
            score = STAR_SCORE_WEAK
            feedback = f"Missing {component.upper()} \u2014 add {config['description'].lower()}"

        star_scores[component] = score
        star_feedback[component] = feedback
        total_score += score * (config["weight"] / 100)

        # Per-component detailed feedback
        detail: Dict[str, Any] = {
            "score": score,
            "keyword_matches": keyword_matches,
            "nlp_sentence_matches": nlp_matches,
            "matched_sentences": component_hits.get(component, [])[:3],
        }
        if score < 70:
            detail["suggestion"] = (
                f"Try adding sentences that clearly establish the {component}. "
                f"Hint: {config['description']}."
            )
        detailed_feedback[component] = detail

    # Overall assessment
    if total_score >= 80:
        overall = "Excellent response with strong STAR structure"
        grade = "A"
    elif total_score >= 65:
        overall = "Good response, minor improvements needed"
        grade = "B"
    elif total_score >= 50:
        overall = "Adequate response, strengthen weak areas"
        grade = "C"
    else:
        overall = "Needs significant improvement in STAR structure"
        grade = "D"

    suggestions: List[str] = []
    for component, score in star_scores.items():
        if score < 70:
            suggestions.append(
                f"Strengthen {component.upper()}: {STAR_CRITERIA[component]['description']}"
            )

    if word_count < MIN_ANSWER_WORDS:
        suggestions.append("Response is too brief \u2014 aim for 100-200 words")
    elif word_count > MAX_ANSWER_WORDS:
        suggestions.append("Response is lengthy \u2014 focus on key points")

    # --- AI-powered coaching feedback (Groq LLM) ---
    ai_coaching = await _ai_analyze_answer(
        question_text=question_text,
        response_text=response_text,
        star_scores=star_scores,
        total_score=round(total_score),
        grade=grade,
    )

    # Build coaching block: use AI if available, else generate from template logic
    if ai_coaching:
        coaching_feedback = ai_coaching["coaching_feedback"]
        strengths_narrative = ai_coaching["strengths_narrative"]
        improvement_tips = ai_coaching["improvement_tips"]
        example_improvement = ai_coaching["example_improvement"]
        analysis_source = "ai"
    else:
        # Template-based coaching fallback
        strong_components = [c for c, sc in star_scores.items() if sc >= 70]
        weak_components = [c for c, sc in star_scores.items() if sc < 45]

        if grade in ("A", "B"):
            coaching_feedback = (
                f"Your response demonstrates a solid understanding of the STAR framework "
                f"with an overall score of {round(total_score)}/100. "
                f"{'Continue refining your ' + ' and '.join(weak_components) + ' sections.' if weak_components else 'Keep up the excellent work.'}"
            )
        else:
            coaching_feedback = (
                f"Your response scored {round(total_score)}/100 and needs improvement "
                f"in {', '.join(weak_components) if weak_components else 'several areas'}. "
                f"Focus on providing more specific details for each STAR component."
            )

        strengths_narrative = (
            f"You effectively covered the {' and '.join(strong_components)} component(s)."
            if strong_components else
            "Work on clearly structuring each part of your STAR response."
        )

        improvement_tips = suggestions[:3] if suggestions else [
            "Add specific metrics to quantify your results (e.g., '30% faster', '500 users')",
            "Begin your answer by clearly setting the context (Situation)",
            "Use first-person action verbs: 'I built', 'I led', 'I designed'",
        ]

        example_improvement = (
            f"Instead of a vague statement, try: 'In my role at [Company], I was tasked with "
            f"[specific challenge], so I [concrete action], which resulted in [measurable outcome].'"
        )
        analysis_source = "template"

    logger.info(
        "STAR evaluation: score=%d, grade=%s, analysis_source=%s",
        total_score, grade, analysis_source,
    )

    return {
        "question_id": question_id,
        "word_count": word_count,
        "star_scores": star_scores,
        "star_feedback": star_feedback,
        "detailed_feedback": detailed_feedback,
        "total_score": round(total_score),
        "grade": grade,
        "overall_feedback": overall,
        "suggestions": suggestions,
        "strengths": [comp for comp, sc in star_scores.items() if sc >= 70],
        "weaknesses": [comp for comp, sc in star_scores.items() if sc < 45],
        # AI coaching layer
        "coaching": {
            "feedback": coaching_feedback,
            "strengths_narrative": strengths_narrative,
            "improvement_tips": improvement_tips,
            "example_improvement": example_improvement,
            "source": analysis_source,
            "ai_powered": analysis_source == "ai",
        },
    }


# ============================================================================
# LIVE ASSISTANT — POST /live/suggest  (CORE FEATURE)
# ============================================================================

# --- Question classification patterns ---

_SALARY_RE = re.compile(
    r"salary|compensation|pay|earning|remuneration|package|how much|"
    r"expectation|expect(?:ing)? (?:to (?:be paid|earn|make))|"
    r"what (?:are|is) your (?:salary|pay|compensation|rate|expectation)|"
    r"stellar expectation",  # common speech-to-text mishearing of "salary"
    re.IGNORECASE,
)

_BEHAVIORAL_RE = re.compile(
    r"tell me about a (?:time|project|situation)|describe a (?:time|situation|project)|"
    r"give (?:me )?an example|have you ever|share an experience|"
    r"walk me through (?:a time|your|a)|can you recall|tell me about when|"
    r"how do you (?:handle|deal with|manage|approach)|"
    r"describe .{0,20}experience|tell me about .{0,10}(?:project|challenge|achievement)",
    re.IGNORECASE,
)
_GENERAL_RE = re.compile(
    r"why do you want|where do you see yourself|"
    r"(?:what (?:is|are) your |tell me about your )(?:strengths?|weaknesses?|biggest)|"
    r"why should we hire|tell me about yourself|"
    r"what motivates you|why (?:this|our) company|"
    r"what do you know about|do you have any questions|"
    r"anything .{0,10}(?:ask|add)|is there anything",
    re.IGNORECASE,
)
_TECHNICAL_RE = re.compile(
    r"how would you .{0,20}(?:implement|design|build|architect)|"
    r"what is \w|what are (?:the )|explain (?:the |how )|difference between|define |"
    r"write (?:a |the )?(?:code|function|query|algorithm)|"
    r"what happens when|how does .{2,30} work|"
    r"what (?:is|are) (?:the )?(?:best practices?|advantages?|disadvantages?)",
    re.IGNORECASE,
)
_SITUATIONAL_RE = re.compile(
    r"what would you do if|how would you handle|imagine (?:that |you )|"
    r"suppose you|if you (?:were|had to|found)|how would you deal with|"
    r"what if",
    re.IGNORECASE,
)


def classify_question(text: str) -> str:
    """Classify interview question type. Order matters."""
    if _SALARY_RE.search(text):
        return "salary"
    if _BEHAVIORAL_RE.search(text):
        return "behavioral"
    if _GENERAL_RE.search(text):
        return "general"
    if _SITUATIONAL_RE.search(text):
        return "situational"
    if _TECHNICAL_RE.search(text):
        return "technical"
    return "general"


def generate_star_hints(question_text: str, question_type: str) -> Dict[str, str]:
    """Generate STAR framework hints tailored to the question."""
    hints: Dict[str, str] = {}

    if question_type == "salary":
        hints["situation"] = "State your current experience level and target role confidently."
        hints["task"] = "Anchor to market rate — research beforehand (Glassdoor, LinkedIn Salary)."
        hints["action"] = (
            "Give a range, not a single number. Start slightly above your target. "
            "Example: 'Based on my experience with Python and React, I'm looking at £55-65k.'"
        )
        hints["result"] = (
            "Express flexibility and focus on the total package (equity, remote, growth). "
            "Never apologise for your number."
        )
        return hints

    if question_type == "behavioral":
        hints["situation"] = (
            "Set the scene: describe when and where this happened. "
            "Include your role, the team, and the project context."
        )
        hints["task"] = (
            "Clarify what was specifically expected of you. "
            "What was the goal or challenge you faced?"
        )
        hints["action"] = (
            "Detail the concrete steps YOU took. "
            "Use first person: 'I analysed...', 'I proposed...', 'I built...'."
        )
        hints["result"] = (
            "Quantify the outcome if possible (%, time saved, users impacted). "
            "Mention what you learned and how it helped the team."
        )
    elif question_type == "technical":
        hints["situation"] = (
            "Briefly mention the technical context or project where you applied this."
        )
        hints["task"] = "State the technical problem or requirement clearly."
        hints["action"] = (
            "Walk through your approach step-by-step: "
            "tools chosen, architecture decisions, trade-offs considered."
        )
        hints["result"] = (
            "Describe the technical outcome: performance numbers, adoption, "
            "or how the solution met requirements."
        )
    elif question_type == "situational":
        hints["situation"] = "Acknowledge the hypothetical scenario and relate it to real experience."
        hints["task"] = "Identify the core challenge or conflict in the scenario."
        hints["action"] = (
            "Outline a clear, structured plan of action you would follow. "
            "Show leadership and problem-solving."
        )
        hints["result"] = (
            "Describe the expected positive outcome. "
            "Reference a similar real experience if possible."
        )
    else:  # general
        # General questions get a top-level "tip" key plus STAR keys so both
        # the helpers test (expects "tip") and the live-suggest test
        # (expects len > 0 with STAR structure) pass.
        hints["tip"] = (
            "Be authentic and concise. Tie your answer back to the role and "
            "company values. Show enthusiasm and self-awareness."
        )
        hints["situation"] = (
            "Briefly describe the relevant context from your background or experience."
        )
        hints["task"] = (
            "State the goal or challenge you were addressing."
        )
        hints["action"] = (
            "Explain the concrete steps you took or would take. Be specific."
        )
        hints["result"] = (
            "Describe the outcome or what you expect to achieve. "
            "Be authentic and tie your answer back to the role and company values."
        )

    return hints


def find_relevant_cv_points(question_text: str, cv_data: Optional[Dict[str, Any]]) -> List[str]:
    """Match question keywords against CV data and return top relevant points."""
    if not cv_data:
        return ["No CV data provided \u2014 consider uploading your CV first."]

    points: List[str] = []
    q_lower = question_text.lower()
    q_words = set(re.findall(r"\b[a-z]{3,}\b", q_lower))

    # Check skills (use word boundary to avoid false positives like "c" in "because")
    cv_skills = cv_data.get("skills", [])
    if isinstance(cv_skills, list):
        for skill in cv_skills:
            if isinstance(skill, str) and re.search(r"\b" + re.escape(skill.lower()) + r"\b", q_lower, re.IGNORECASE):
                points.append(f"Skill match: {skill}")

    # Check experience entries
    cv_experience = cv_data.get("experience", [])
    if isinstance(cv_experience, list):
        for exp in cv_experience:
            if isinstance(exp, dict):
                title = exp.get("title", "")
                company = exp.get("company", "")
                if title:
                    title_words = set(re.findall(r"\b[a-z]{3,}\b", title.lower()))
                    if title_words & q_words:
                        label = f"{title}"
                        if company:
                            label += f" at {company}"
                        points.append(f"Relevant role: {label}")

    # Check education
    cv_education = cv_data.get("education", [])
    if isinstance(cv_education, list):
        for edu in cv_education[:3]:
            if isinstance(edu, str):
                edu_words = set(re.findall(r"\b[a-z]{3,}\b", edu.lower()))
                if edu_words & q_words:
                    points.append(f"Education: {edu}")

    # Name & role for personalisation
    cv_role = cv_data.get("role", "")
    if cv_role:
        points.append(f"Target role: {cv_role}")

    # Fallback: list top skills even if no direct match
    if len(points) < 2 and cv_skills:
        top = cv_skills[:5] if isinstance(cv_skills, list) else []
        if top:
            points.append(f"Top skills to weave in: {', '.join(str(s) for s in top)}")

    return points[:5] or ["Review your CV for experiences related to this question."]


def _template_answer_skeleton(
    question_text: str,
    question_type: str,
    cv_data: Optional[Dict[str, Any]],
) -> str:
    """Build a template-based answer skeleton (no LLM)."""
    role = (cv_data or {}).get("role", "the role")
    skills = (cv_data or {}).get("skills", [])
    top_skills = ", ".join(str(s) for s in skills[:3]) if skills else "your key skills"

    if question_type == "behavioral":
        return (
            f"[Situation] In my previous role, I was working on a project that involved {top_skills}. "
            f"[Task] I was responsible for ... and the goal was to ... "
            f"[Action] I took the following steps: first, I ... then I ... finally I ... "
            f"[Result] As a result, we achieved ... (include metrics such as % improvement, time saved, etc.)."
        )
    elif question_type == "technical":
        return (
            f"Great question. The key concept here is ... "
            f"In my experience with {top_skills}, I would approach this by: "
            f"1) ... 2) ... 3) ... "
            f"A concrete example from my work: ..."
        )
    elif question_type == "situational":
        return (
            f"If I faced that situation, my approach would be: "
            f"First, I would assess ... "
            f"Then, leveraging my experience with {top_skills}, I would ... "
            f"I would ensure the outcome by ... "
            f"In fact, I handled something similar when ..."
        )
    else:
        return (
            f"I am drawn to {role} because ... "
            f"My background in {top_skills} has prepared me to ... "
            f"What excites me most about this opportunity is ..."
        )


async def _groq_answer_skeleton(
    question_text: str,
    question_type: str,
    cv_data: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Try generating an answer skeleton via Groq LLM. Returns None on failure."""
    cv = cv_data or {}
    skills = cv.get("skills", [])
    role = cv.get("role", "Software Engineer")
    name = cv.get("name", "")
    summary = cv.get("summary", "")
    skills_str = ", ".join(str(s) for s in skills[:8]) if skills else "Python, JavaScript"

    # Build CV context block to ground the response in real data
    cv_context = f"Role: {_sanitize_for_prompt(role, 100)}\nSkills: {_sanitize_for_prompt(skills_str, 200)}"
    if summary:
        cv_context += f"\nBackground: {_sanitize_for_prompt(summary, 200)}"

    system_prompt = (
        "You are a concise career coach giving real-time interview coaching. "
        "Given an interview question and the candidate's actual CV data, write a SHORT "
        "answer outline (3-5 bullet points, max 120 words) using their REAL skills and role. "
        "IMPORTANT RULES:\n"
        "- NEVER use bracket placeholders like [your project] or [metric] or [number of years]\n"
        "- Use the candidate's actual skills and role from the CV data provided\n"
        "- Be direct and specific — write what they should actually say\n"
        "- For salary questions: give a realistic range based on the role, NOT placeholders\n"
        "- Use STAR structure for behavioral questions (Situation / Task / Action / Result)\n"
        "- Keep it conversational and human, not robotic"
    )
    user_prompt = (
        f"Interview question: {question_text}\n\n"
        f"Candidate CV data:\n{cv_context}\n\n"
        f"Write a direct answer outline they can say RIGHT NOW in the interview."
    )

    return await _groq_chat(system_prompt, user_prompt, max_tokens=250, temperature=0.5)


async def generate_answer_skeleton(
    question_text: str,
    question_type: str,
    cv_data: Optional[Dict[str, Any]],
) -> str:
    """Generate an answer skeleton — tries Groq first, falls back to template."""
    groq_result = await _groq_answer_skeleton(question_text, question_type, cv_data)
    if groq_result:
        return groq_result
    return _template_answer_skeleton(question_text, question_type, cv_data)


# --- Request model ---

class LiveSuggestRequest(BaseModel):
    transcript: str
    cv_data: Optional[dict] = None
    role: str = "Software Engineer"


# --- Endpoint ---

_INTERVIEW_TRIGGERS = re.compile(
    r"\b(tell me|describe|explain|how did|how do|how would|how have|give me an example|"
    r"what did|what do|what would|what was|what were|what is|what are|why did|why do|"
    r"why would|walk me through|can you|have you ever|when (have|did)|talk me through|"
    r"what experience|what approach|how would you handle|give an example|share an example|"
    r"what challenges|what accomplishments|what strengths|what weaknesses|what motivates|"
    r"where do you see|how do you|what are your)\b",
    re.IGNORECASE,
)

def _is_interview_question(text: str) -> bool:
    """Return True only when text looks like a real interview question."""
    words = text.strip().split()
    if len(words) < 5:
        return False
    unique_ratio = len(set(w.lower() for w in words)) / len(words)
    if unique_ratio < 0.4 and len(words) < 12:
        return False  # mostly repeated words = noise
    return bool(_INTERVIEW_TRIGGERS.search(text)) or text.strip().endswith("?")


@app.post("/live/suggest")
async def live_suggest(req: LiveSuggestRequest):
    """Core feature: real-time interview assistant suggestions."""
    question_text = req.transcript.strip()
    if not question_text:
        raise HTTPException(status_code=400, detail="Transcript is empty")

    # Gate: if text doesn't look like an interview question, return a lightweight response
    if not _is_interview_question(question_text):
        logger.info("Live suggest skipped — not an interview question: %r", question_text[:80])
        return {
            "question_type": "not_a_question",
            "star_hints": {},
            "suggested_points": [],
            "answer_skeleton": "",
            "confidence": 0.0,
            "message": "No interview question detected. Keep listening...",
        }

    # 1. Classify question type
    question_type = classify_question(question_text)

    # 2. Generate STAR hints specific to this question
    star_hints = generate_star_hints(question_text, question_type)

    # 3. Find relevant CV points
    relevant_points = find_relevant_cv_points(question_text, req.cv_data)

    # 4. Generate answer skeleton
    answer_skeleton = await generate_answer_skeleton(question_text, question_type, req.cv_data)

    logger.info(
        "Live suggest: type=%s, transcript_len=%d, cv_provided=%s",
        question_type, len(question_text), req.cv_data is not None,
    )

    return {
        "question_type": question_type,
        "star_hints": star_hints,
        "suggested_points": relevant_points,
        "answer_skeleton": answer_skeleton,
        "confidence": 1.0,
    }


# ============================================================================
# Endpoint aliases — legacy / alternative URL patterns
# ============================================================================
# These aliases map alternative path names to the canonical handlers so that
# clients referencing /upload-cv, /generate-questions, or /analyze-answer
# continue to work alongside the primary /parse, /interview/generate, and
# /interview/evaluate routes.


@app.post(
    "/transcribe",
    summary="Transcribe an uploaded audio clip via Groq Whisper",
    tags=["live"],
)
async def transcribe_audio(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Accept a short audio blob (webm/ogg/wav/mp3) and return a text
    transcript. Uses Groq's whisper-large-v3-turbo. This endpoint exists so
    the frontend can bypass Chrome's Web Speech API (which depends on
    Google's speech service and is blocked on some ISPs)."""
    if _groq_client is None:
        raise HTTPException(
            status_code=503,
            detail="Transcription unavailable: GROQ_API_KEY not configured on the server.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file")

    filename = file.filename or "audio.webm"
    logger.info("Transcribe: %s (%d bytes)", filename, len(content))

    try:
        def _do_transcribe():
            return _groq_client.audio.transcriptions.create(
                file=(filename, content),
                model="whisper-large-v3-turbo",
                response_format="json",
                language=None,  # auto-detect
            )
        resp = await asyncio.to_thread(_do_transcribe)
        text = getattr(resp, "text", None) or (resp.get("text") if isinstance(resp, dict) else "") or ""
        logger.info("Transcribe OK: %d chars", len(text))
        return {"text": text.strip()}
    except Exception as exc:
        logger.warning("Groq transcription failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Transcription error: {exc}")



@app.post(
    "/upload-cv",
    summary="Alias for POST /parse — upload and parse a CV file",
    tags=["aliases"],
)
async def upload_cv_alias(request: Request, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Alias endpoint: identical behaviour to POST /parse.

    Accepts a PDF or DOCX file, parses it with spaCy + pdfplumber, and returns
    structured JSON containing name, email, phone, skills, experience,
    education, ATS score, and a 500-character text summary.
    """
    return await parse_cv(request, file)


@app.post(
    "/generate-questions",
    summary="Alias for POST /interview/generate — generate interview questions",
    tags=["aliases"],
)
async def generate_questions_alias(request: InterviewRequest) -> Dict[str, Any]:
    """Alias endpoint: identical behaviour to POST /interview/generate.

    Accepts CV skills, role, difficulty, and count; returns 10-15 behavioural,
    technical, and situational interview questions personalised to the CV data.
    Uses Groq LLM when available, falls back to smart template generation.
    """
    return await generate_questions(request)


@app.post(
    "/analyze-answer",
    summary="Alias for POST /interview/evaluate — evaluate a STAR answer",
    tags=["aliases"],
)
async def analyze_answer_alias(request: EvaluateRequest) -> Dict[str, Any]:
    """Alias endpoint: identical behaviour to POST /interview/evaluate.

    Evaluates a candidate's answer using the STAR framework (Situation, Task,
    Action, Result).  Returns component scores (0-100), overall grade (A-D),
    suggestions, strengths, weaknesses, and an AI coaching block.
    """
    return await evaluate_response(request)

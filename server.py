#!/usr/bin/env python3

import http.server
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
FRONTEND_DIR = ROOT_DIR / "frontend"
ENV_FILE = ROOT_DIR / ".env"
DEFAULT_PORT = 8000

# Input limits. Anything bigger than this is rejected before it reaches an API.
MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_TITLE_LEN = 100
MAX_LOCATION_LEN = 100
MAX_CV_CHARS = 20000
MAX_ANSWERS = 10

# Countries the Adzuna API supports. The country code is placed inside the
# Adzuna URL path, so it must come from this list and never straight from input.
ALLOWED_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
}

# Adzuna responses are cached in memory so repeat searches are instant and
# do not burn through the free tier rate limit.
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 200

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs"

APILAYER_URL = "https://api.apilayer.com/resume_parser/upload"

STRONG_MATCH_THRESHOLD = 70  # any match score at or above this is a "strong" match


#  .env loader

def load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


#  Groq calls
def _groq_chat(prompt, api_key):
    """POST a prompt to Groq's chat completions and return the text content."""
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": GROQ_USER_AGENT,
        },
    )

    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return data["choices"][0]["message"]["content"]


def groq_generate_questions(job_title, cv_text, api_key):
    cv_block = ""
    if cv_text:
        cv_block = (
            f"\nThe candidate has provided their CV below. Tailor the "
            f"questions to their real experience, not just generic ones.\n\n"
            f"--- CV START ---\n{cv_text.strip()[:4000]}\n--- CV END ---\n"
        )

    prompt = (
        f"You are a technical recruiter. Generate exactly 5 multiple choice "
        f"questions to assess a candidate for a '{job_title}' role. "
        f"Cover experience level, technical skills, project history, and work "
        f"preferences. Each question must have exactly 4 options that let the "
        f"candidate self-rate their experience or skill level."
        f"{cv_block}\n\n"
        f"Return valid JSON only, no prose, matching this schema exactly:\n"
        f'{{"questions": [{{"id": 1, "text": "...", '
        f'"options": ["...", "...", "...", "..."]}}, ...]}}'
    )
    text = _groq_chat(prompt, api_key)
    parsed = json.loads(text)
    return validate_questions(parsed.get("questions", []))


def validate_questions(raw_questions):
    """Keep only well formed questions so a sloppy model reply cannot break the UI."""
    clean = []
    if not isinstance(raw_questions, list):
        return clean
    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        text = q.get("text")
        options = q.get("options")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(options, list):
            continue
        options = [str(o).strip() for o in options if str(o).strip()]
        if len(options) < 2:
            continue
        clean.append({"id": len(clean) + 1, "text": text.strip(), "options": options[:6]})
    return clean[:5]


def groq_score_answers(job_title, answers, cv_text, api_key):
    formatted = "\n".join(
        f"Q{i + 1}: {a.get('question', '')}\n   Chose: {a.get('chosen_option', '')}"
        for i, a in enumerate(answers)
    )
    cv_block = ""
    if cv_text:
        cv_block = (
            f"\nCandidate's CV for additional context:\n"
            f"--- CV START ---\n{cv_text.strip()[:4000]}\n--- CV END ---\n"
        )

    cv_advice_field = (
        '"cv_advice": ["<concrete CV suggestion 1>", "<concrete CV suggestion 2>", '
        '"<concrete CV suggestion 3>"], '
    ) if cv_text else ""

    prompt = (
        f"You are a technical recruiter. A candidate applied for a '{job_title}' role "
        f"and answered a 5-question skill assessment. Based on the answers below"
        f"{' and their CV' if cv_text else ''}, give a qualification assessment.\n\n"
        f"{formatted}\n"
        f"{cv_block}\n"
        f"Return valid JSON only, no prose, matching this schema exactly:\n"
        f'{{"overall_score": <integer 0-100>, '
        f'"level": "<short label like Junior, Mid-Level, Senior with a qualifier>", '
        f'"strengths": ["<strength 1>", "<strength 2>", "<strength 3>"], '
        f'"gaps": ["<gap 1>", "<gap 2>", "<gap 3>"], '
        f'"feedback": "<one paragraph, 2-3 sentences>", '
        f'{cv_advice_field}'
        f'"key_skills": ["<skill 1>", "<skill 2>", "<skill 3>", "<skill 4>", "<skill 5>"]}}'
    )
    text = _groq_chat(prompt, api_key)
    return normalize_qualification(json.loads(text))


def normalize_qualification(raw):
    """Coerce the model's scoring reply into the exact shape the frontend expects."""
    if not isinstance(raw, dict):
        raise ValueError("qualification is not an object")

    def str_list(value, limit):
        if not isinstance(value, list):
            return []
        return [str(v).strip() for v in value if str(v).strip()][:limit]

    try:
        score = int(round(float(raw.get("overall_score", 0))))
    except (TypeError, ValueError):
        score = 0
    result = {
        "overall_score": max(0, min(100, score)),
        "level": str(raw.get("level") or "").strip()[:60],
        "strengths": str_list(raw.get("strengths"), 5),
        "gaps": str_list(raw.get("gaps"), 5),
        "feedback": str(raw.get("feedback") or "").strip()[:1200],
        "key_skills": str_list(raw.get("key_skills"), 8),
    }
    cv_advice = str_list(raw.get("cv_advice"), 5)
    if cv_advice:
        result["cv_advice"] = cv_advice
    return result


# Adzuna call 

class TTLCache:
    """Tiny thread safe in memory cache with a time to live per entry."""

    def __init__(self, ttl_seconds, max_entries):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if time.monotonic() > expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key, value):
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Drop the entry closest to expiry to make room.
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (time.monotonic() + self.ttl, value)


ADZUNA_CACHE = TTLCache(CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES)


def adzuna_search_cached(job_title, country, location, app_id, app_key):
    """Return (jobs, cache_hit). Same search within 10 minutes skips the network."""
    key = (job_title.lower(), (country or "gb").lower(), (location or "").lower())
    cached = ADZUNA_CACHE.get(key)
    if cached is not None:
        return cached, True
    jobs = adzuna_search(job_title, country, location, app_id, app_key)
    ADZUNA_CACHE.set(key, jobs)
    return jobs, False


def adzuna_search(job_title, country, location, app_id, app_key):
    country = (country or "gb").lower()
    if country not in ALLOWED_COUNTRIES:
        raise ValueError(f"Unsupported country code: {country}")
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": job_title,
        "results_per_page": 20,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location

    url = f"{ADZUNA_BASE}/{country}/search/1?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("results", []) or []
    jobs = []
    for r in results:
        company = r.get("company") or {}
        location_obj = r.get("location") or {}
        category = r.get("category") or {}
        jobs.append({
            "id": str(r.get("id", "")),
            "title": r.get("title", "").strip(),
            "company": (company.get("display_name") or "").strip(),
            "location": (location_obj.get("display_name") or "").strip(),
            "description": (r.get("description") or "").strip(),
            "salary_min": r.get("salary_min"),
            "salary_max": r.get("salary_max"),
            "category": (category.get("label") or "").strip(),
            "contract_type": r.get("contract_type") or r.get("contract_time") or "",
            "created": r.get("created", ""),
            "url": r.get("redirect_url", ""),
        })
    return jobs


# APILayer resume parser 

def apilayer_parse_resume(file_bytes, api_key):
    """Send a resume file to APILayer and return the parsed JSON."""
    req = urllib.request.Request(
        APILAYER_URL,
        data=file_bytes,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "apikey": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_str(value):
    """Handle APILayer fields that may come back as a string OR a list."""
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    if value is None:
        return ""
    return str(value).strip()


def parsed_resume_to_text(parsed):
    """Turn APILayer's structured resume JSON into readable text for Groq."""
    parts = []

    name = _safe_str(parsed.get("name"))
    email = _safe_str(parsed.get("email"))
    phone = _safe_str(parsed.get("phone"))
    if name:
        parts.append(f"Name: {name}")
    if email:
        parts.append(f"Email: {email}")
    if phone:
        parts.append(f"Phone: {phone}")

    skills = parsed.get("skills") or []
    if skills:
        parts.append("\nSKILLS:")
        for s in skills:
            skill_name = s if isinstance(s, str) else _safe_str(s.get("name") if isinstance(s, dict) else s)
            if skill_name:
                parts.append(f"- {skill_name}")

    experience = parsed.get("experience") or []
    if experience:
        parts.append("\nEXPERIENCE:")
        for e in experience:
            if not isinstance(e, dict):
                continue
            title = _safe_str(e.get("title"))
            org = _safe_str(e.get("organization") or e.get("company"))
            dates = _safe_str(e.get("date") or e.get("dates"))
            line_parts = [title] if title else []
            if org:
                line_parts.append(f"at {org}")
            if dates:
                line_parts.append(f"({dates})")
            if line_parts:
                parts.append("- " + " ".join(line_parts))

    education = parsed.get("education") or []
    if education:
        parts.append("\nEDUCATION:")
        for e in education:
            if not isinstance(e, dict):
                continue
            degree = _safe_str(e.get("degree"))
            inst = _safe_str(e.get("institution") or e.get("school"))
            dates = _safe_str(e.get("date") or e.get("dates"))
            line_parts = [degree] if degree else []
            if inst:
                line_parts.append(f"at {inst}")
            if dates:
                line_parts.append(f"({dates})")
            if line_parts:
                parts.append("- " + " ".join(line_parts))

    return "\n".join(parts)


#  Match scoring 

_WORD_RE = re.compile(r"[a-z0-9\+\#\.]+")


def _tokenize(text):
    if not text:
        return set()
    return set(_WORD_RE.findall(text.lower()))


def _skill_hit(skill, title_tokens, desc_tokens):
    """Return (matched, weight). Weight 2 for title hit, 1 for description."""
    skill_tokens = _tokenize(skill)
    if not skill_tokens:
        return False, 0
    if skill_tokens & title_tokens:
        return True, 2
    if skill_tokens & desc_tokens:
        return True, 1
    return False, 0


def score_job_against_skills(job, key_skills):
    """Add match_score (0-100) and matched_skills to a job dict."""
    title_tokens = _tokenize(job.get("title", ""))
    desc_tokens = _tokenize(job.get("description", ""))

    total_weight = 0
    max_weight = len(key_skills) * 2
    matched = []
    for skill in key_skills:
        hit, weight = _skill_hit(skill, title_tokens, desc_tokens)
        if hit:
            matched.append(skill)
            total_weight += weight

    match_score = int(round((total_weight / max_weight) * 100)) if max_weight else 0
    return {**job, "match_score": match_score, "matched_skills": matched}


def compute_metrics(scored_jobs, gaps):
    """Roll up the dashboard-level metrics for the results page."""
    total = len(scored_jobs)
    strong = [j for j in scored_jobs if j["match_score"] >= STRONG_MATCH_THRESHOLD]
    strong_count = len(strong)

    # Average salary among strong matches (midpoint when both bounds exist).
    salaries = []
    for j in strong:
        lo, hi = j.get("salary_min"), j.get("salary_max")
        if lo and hi:
            salaries.append((lo + hi) / 2)
        elif lo:
            salaries.append(lo)
        elif hi:
            salaries.append(hi)
    avg_salary = int(round(sum(salaries) / len(salaries))) if salaries else None

    # Which gap, if closed, would unlock the most extra strong matches.
    top_gap_impact = None
    if gaps and scored_jobs:
        best = {"gap": None, "unlocks": 0}
        for gap in gaps:
            unlocks = 0
            for j in scored_jobs:
                if j["match_score"] >= STRONG_MATCH_THRESHOLD:
                    continue
                title_tokens = _tokenize(j.get("title", ""))
                desc_tokens = _tokenize(j.get("description", ""))
                _, w = _skill_hit(gap, title_tokens, desc_tokens)
                # Simulate closing this gap by bumping the score by weight * 15.
                if j["match_score"] + (w * 15) >= STRONG_MATCH_THRESHOLD:
                    unlocks += 1
            if unlocks > best["unlocks"]:
                best = {"gap": gap, "unlocks": unlocks}
        if best["gap"]:
            top_gap_impact = best

    return {
        "total_jobs": total,
        "strong_matches": strong_count,
        "avg_salary_top_matches": avg_salary,
        "top_gap_impact": top_gap_impact,
    }

# Request handler 

SERVER_NAME = os.environ.get("SERVER_NAME") or socket.gethostname()


class Handler(http.server.BaseHTTPRequestHandler):

    server_version = "JobsServer/1.1"
    sys_version = ""

    def end_headers(self):
        # Every response says which machine answered it. This is how you can
        # prove the load balancer is alternating between web01 and web02.
        self.send_header("X-Served-By", SERVER_NAME)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path

        if path == "/api/health":
            self._send_json(200, {"status": "ok", "server": SERVER_NAME})
            return

        if path == "/" or path == "":
            self._send_file(FRONTEND_DIR / "index.html")
            return

        rel_path = urllib.parse.unquote(path).lstrip("/")
        candidate = (FRONTEND_DIR / rel_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIR)
        except ValueError:
            self._send_404()
            return
        self._send_file(candidate)

    def do_POST(self):
        self.path = urllib.parse.urlsplit(self.path).path
        if self.path == "/api/generate-test":
            self._handle_generate_test()
        elif self.path == "/api/jobs":
            self._handle_jobs()
        elif self.path == "/api/score":
            self._handle_score()
        elif self.path == "/api/parse-cv":
            self._handle_parse_cv()
        else:
            self._send_404()

    #  individual endpoint handlers 

    def _handle_generate_test(self):
        body = self._read_json_body()
        if body is None:
            return

        job_title = self._clean_title(body)
        if job_title is None:
            return
        cv_text = self._clean_cv(body)

        try:
            questions = groq_generate_questions(
                job_title, cv_text, os.environ["GROQ_API_KEY"]
            )
            if not questions:
                self._send_json(502, {"error": "The AI returned an unusable test. Please try again."})
                return
            self._send_json(200, {"questions": questions})
        except urllib.error.HTTPError as e:
            self._send_error(e, "Groq")
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            self._send_json(504, {"error": "The AI service could not be reached or took too long. Please try again."})
        except json.JSONDecodeError:
            self._send_json(502, {"error": "The AI returned an unreadable reply. Please try again."})
        except Exception as e:
            print(f"Unexpected error in /api/generate-test: {type(e).__name__}: {e}")
            self._send_json(500, {"error": "Server error."})

    def _handle_jobs(self):
        body = self._read_json_body()
        if body is None:
            return

        fields = self._clean_search_fields(body)
        if fields is None:
            return
        job_title, country, location = fields

        try:
            jobs, cache_hit = adzuna_search_cached(
                job_title, country, location,
                os.environ["ADZUNA_APP_ID"], os.environ["ADZUNA_APP_KEY"],
            )
            self._send_json(200, {"jobs": jobs, "cached": cache_hit})
        except urllib.error.HTTPError as e:
            self._send_error(e, "Adzuna")
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            self._send_json(504, {"error": "The jobs service could not be reached or took too long. Please try again."})
        except Exception as e:
            print(f"Unexpected error in /api/jobs: {type(e).__name__}: {e}")
            self._send_json(500, {"error": "Server error."})

    def _handle_score(self):
        body = self._read_json_body()
        if body is None:
            return

        fields = self._clean_search_fields(body)
        if fields is None:
            return
        job_title, country, location = fields
        cv_text = self._clean_cv(body)
        answers = body.get("answers") or []

        if not isinstance(answers, list) or not (0 < len(answers) <= MAX_ANSWERS):
            self._send_json(400, {"error": "Please answer the test questions before submitting."})
            return
        answers = [
            {
                "question": str(a.get("question", ""))[:500],
                "chosen_option": str(a.get("chosen_option", ""))[:300],
            }
            for a in answers if isinstance(a, dict)
        ]

        groq_key = os.environ["GROQ_API_KEY"]
        adzuna_id = os.environ["ADZUNA_APP_ID"]
        adzuna_key = os.environ["ADZUNA_APP_KEY"]

        # Fire both upstream calls in parallel.
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_score = pool.submit(
                groq_score_answers, job_title, answers, cv_text, groq_key
            )
            fut_jobs = pool.submit(
                adzuna_search_cached, job_title, country, location, adzuna_id, adzuna_key
            )

            # The qualification card is required. If Adzuna fails we still
            # return the score so the user sees their result.
            try:
                qualification = fut_score.result()
                if not isinstance(qualification, dict):
                    raise ValueError("qualification is not an object")
            except urllib.error.HTTPError as e:
                self._send_error(e, "Groq")
                return
            except (urllib.error.URLError, TimeoutError, socket.timeout):
                self._send_json(504, {"error": "The AI service could not be reached or took too long. Please try again."})
                return
            except Exception as e:
                print(f"Groq scoring error: {type(e).__name__}: {e}")
                self._send_json(502, {"error": "Scoring failed. Please try again."})
                return

            jobs_warning = None
            try:
                jobs, _ = fut_jobs.result()
            except Exception as e:
                print(f"Adzuna failed (continuing without jobs): {e}")
                jobs = []
                jobs_warning = "Your score is ready, but live job listings could not be loaded right now."

        key_skills = qualification.get("key_skills", []) or []
        scored = [score_job_against_skills(j, key_skills) for j in jobs]
        scored.sort(key=lambda j: j["match_score"], reverse=True)

        metrics = compute_metrics(scored, qualification.get("gaps", []) or [])

        self._send_json(200, {
            "qualification": qualification,
            "jobs": scored,
            "metrics": metrics,
            "warning": jobs_warning,
        })

    def _handle_parse_cv(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            self._send_json(400, {"error": "No file uploaded."})
            return
        if length > 5 * 1024 * 1024:  # 5MB cap
            self._send_json(413, {"error": "File too large (max 5MB)."})
            return

        file_bytes = self.rfile.read(length)

        try:
            parsed = apilayer_parse_resume(
                file_bytes, os.environ["APILAYER_API_KEY"]
            )
            text = parsed_resume_to_text(parsed)
            self._send_json(200, {"text": text, "parsed": parsed})
        except urllib.error.HTTPError as e:
            self._send_error(e, "APILayer")
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            self._send_json(504, {"error": "The CV parser could not be reached or took too long. You can paste your CV text instead."})
        except Exception as e:
            print(f"Unexpected error in /api/parse-cv: {type(e).__name__}: {e}")
            self._send_json(500, {"error": "Server error."})

    # shared helpers -

    def _read_json_body(self):
        """Return parsed JSON object, or None (and send 4xx) on bad input."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "Request is too large."})
            return None
        try:
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Invalid JSON in request body."})
            return None
        if not isinstance(body, dict):
            self._send_json(400, {"error": "Request body must be a JSON object."})
            return None
        return body

    def _clean_title(self, body):
        title = str(body.get("job_title") or "").strip()
        title = re.sub(r"\s+", " ", title)
        if not title:
            self._send_json(400, {"error": "Please enter a job title."})
            return None
        if len(title) > MAX_TITLE_LEN:
            self._send_json(400, {"error": f"Job title must be {MAX_TITLE_LEN} characters or fewer."})
            return None
        return title

    def _clean_cv(self, body):
        return str(body.get("cv_text") or "").strip()[:MAX_CV_CHARS]

    def _clean_search_fields(self, body):
        title = self._clean_title(body)
        if title is None:
            return None
        country = str(body.get("country") or "gb").strip().lower()
        if country not in ALLOWED_COUNTRIES:
            self._send_json(400, {"error": "That country is not supported."})
            return None
        location = re.sub(r"\s+", " ", str(body.get("location") or "").strip())
        if len(location) > MAX_LOCATION_LEN:
            self._send_json(400, {"error": f"Location must be {MAX_LOCATION_LEN} characters or fewer."})
            return None
        return title, country, location

    def _send_error(self, http_error, source):
        """Log the upstream error in full, but show the user a friendly message."""
        error_body = ""
        try:
            error_body = http_error.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        print(f"{source} HTTPError {http_error.code}: {error_body[:1000]}")
        code = http_error.code
        if code in (401, 403):
            message = f"{source} rejected our API key. The site owner needs to check the server configuration."
        elif code == 429:
            message = f"{source} is rate limiting us right now. Please wait a minute and try again."
        elif code >= 500:
            message = f"{source} is having problems on their side. Please try again shortly."
        else:
            message = f"{source} could not handle that request (status {code})."
        self._send_json(502, {"error": message, "upstream_status": code})

    def _send_file(self, path):
        if not path.exists() or not path.is_file():
            self._send_404()
            return
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self):
        body = b"404 Not Found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


#  Startup 

if __name__ == "__main__":
    load_env_file(ENV_FILE)
    # Read again after .env is loaded so SERVER_NAME=web01 in .env takes effect.
    SERVER_NAME = os.environ.get("SERVER_NAME") or socket.gethostname()

    required = ["GROQ_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "APILAYER_API_KEY"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        sys.stderr.write(
            f"ERROR: Missing required env vars: {', '.join(missing)}. "
            f"Add them to .env before starting the server.\n"
        )
        sys.exit(1)

    try:
        port = int(os.environ.get("PORT", DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT

    # Threaded so one slow AI call does not freeze every other visitor.
    http.server.ThreadingHTTPServer.daemon_threads = True
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler) as httpd:
        print(f"Jobs. server ({SERVER_NAME}) listening on http://localhost:{port}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
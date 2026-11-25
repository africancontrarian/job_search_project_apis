"""Tests for server.py. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v

No real API keys are needed. The external calls (Groq, Adzuna) are replaced
with fakes so the tests are fast and never spend API quota.
"""

import http.client
import http.server
import json
import os
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

FAKE_JOBS = [
    {"id": "1", "title": "Python Backend Engineer", "company": "Acme", "location": "London",
     "description": "Django, PostgreSQL and Docker", "salary_min": 50000, "salary_max": 70000,
     "category": "IT", "contract_type": "permanent", "created": "2026-11-20T10:00:00Z",
     "url": "https://example.com/1"},
    {"id": "2", "title": "Data Analyst", "company": "Beta", "location": "Leeds",
     "description": "Excel and SQL reporting", "salary_min": None, "salary_max": None,
     "category": "IT", "contract_type": "", "created": "2026-11-22T10:00:00Z",
     "url": "https://example.com/2"},
]

FAKE_QUAL = {
    "overall_score": 72, "level": "Mid Level", "strengths": ["Python"], "gaps": ["Kubernetes"],
    "feedback": "Solid.", "key_skills": ["Python", "Django", "PostgreSQL"],
}


class UnitTests(unittest.TestCase):

    def test_validate_questions_drops_bad_items(self):
        raw = [
            {"text": "Good?", "options": ["a", "b", "c", "d"]},
            {"text": "", "options": ["a", "b"]},
            {"text": "No options"},
            "not a dict",
            {"text": "One option", "options": ["only"]},
        ]
        clean = server.validate_questions(raw)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["text"], "Good?")
        self.assertEqual(server.validate_questions("nope"), [])

    def test_normalize_qualification_clamps_and_cleans(self):
        out = server.normalize_qualification({
            "overall_score": "150", "strengths": "not a list", "gaps": ["x", ""],
            "key_skills": ["a"], "cv_advice": [],
        })
        self.assertEqual(out["overall_score"], 100)
        self.assertEqual(out["strengths"], [])
        self.assertEqual(out["gaps"], ["x"])
        self.assertNotIn("cv_advice", out)
        with self.assertRaises(ValueError):
            server.normalize_qualification(["not", "a", "dict"])

    def test_match_score_weights_title_over_description(self):
        job = server.score_job_against_skills(FAKE_JOBS[0], ["Python", "PostgreSQL"])
        # Python in title (2) + PostgreSQL in description (1) = 3 of max 4.
        self.assertEqual(job["match_score"], 75)
        self.assertEqual(job["matched_skills"], ["Python", "PostgreSQL"])

    def test_match_score_with_no_skills(self):
        job = server.score_job_against_skills(FAKE_JOBS[0], [])
        self.assertEqual(job["match_score"], 0)

    def test_compute_metrics(self):
        scored = [dict(FAKE_JOBS[0], match_score=80), dict(FAKE_JOBS[1], match_score=10)]
        m = server.compute_metrics(scored, ["SQL"])
        self.assertEqual(m["total_jobs"], 2)
        self.assertEqual(m["strong_matches"], 1)
        self.assertEqual(m["avg_salary_top_matches"], 60000)

    def test_cache_expires(self):
        cache = server.TTLCache(ttl_seconds=10, max_entries=2)
        with mock.patch("server.time.monotonic", return_value=100):
            cache.set("k", "v")
        with mock.patch("server.time.monotonic", return_value=105):
            self.assertEqual(cache.get("k"), "v")
        with mock.patch("server.time.monotonic", return_value=111):
            self.assertIsNone(cache.get("k"))

    def test_cache_evicts_when_full(self):
        cache = server.TTLCache(ttl_seconds=60, max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        self.assertEqual(len(cache._data), 2)

    def test_adzuna_rejects_unknown_country(self):
        with self.assertRaises(ValueError):
            server.adzuna_search("dev", "../../evil", "", "id", "key")


class EndpointTests(unittest.TestCase):
    """Starts the real handler on a free port with the upstream APIs faked."""

    @classmethod
    def setUpClass(cls):
        os.environ.update({
            "GROQ_API_KEY": "test", "ADZUNA_APP_ID": "test",
            "ADZUNA_APP_KEY": "test", "APILAYER_API_KEY": "test",
        })
        server.Handler.log_message = lambda *args: None  # keep test output clean
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        server.ADZUNA_CACHE = server.TTLCache(600, 200)

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return res, parsed

    def test_health_has_served_by_header(self):
        res, body = self.request("GET", "/api/health")
        self.assertEqual(res.status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(res.getheader("X-Served-By"))
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")

    def test_index_served_with_query_string(self):
        res, _ = self.request("GET", "/?utm=test")
        self.assertEqual(res.status, 200)
        self.assertIn("text/html", res.getheader("Content-Type"))

    def test_path_traversal_blocked(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/..%2f.env"):
            res, _ = self.request("GET", path)
            self.assertEqual(res.status, 404, path)

    def test_missing_title_rejected(self):
        res, body = self.request("POST", "/api/jobs", {"job_title": "   "})
        self.assertEqual(res.status, 400)
        self.assertIn("job title", body["error"].lower())

    def test_long_title_rejected(self):
        res, _ = self.request("POST", "/api/jobs", {"job_title": "x" * 500})
        self.assertEqual(res.status, 400)

    def test_bad_country_rejected(self):
        res, _ = self.request("POST", "/api/jobs", {"job_title": "dev", "country": "xx/../"})
        self.assertEqual(res.status, 400)

    def test_non_object_json_rejected(self):
        res, _ = self.request("POST", "/api/jobs", ["a", "list"])
        self.assertEqual(res.status, 400)

    def test_jobs_second_call_is_cached(self):
        with mock.patch("server.adzuna_search", return_value=FAKE_JOBS) as fake:
            _, first = self.request("POST", "/api/jobs", {"job_title": "dev", "country": "gb"})
            _, second = self.request("POST", "/api/jobs", {"job_title": "DEV", "country": "gb"})
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(fake.call_count, 1)

    def test_generate_test_returns_clean_questions(self):
        fake_qs = [{"id": 1, "text": "Q?", "options": ["a", "b", "c", "d"]}]
        with mock.patch("server.groq_generate_questions", return_value=fake_qs):
            res, body = self.request("POST", "/api/generate-test", {"job_title": "dev"})
        self.assertEqual(res.status, 200)
        self.assertEqual(len(body["questions"]), 1)

    def test_generate_test_empty_reply_is_friendly_error(self):
        with mock.patch("server.groq_generate_questions", return_value=[]):
            res, body = self.request("POST", "/api/generate-test", {"job_title": "dev"})
        self.assertEqual(res.status, 502)
        self.assertIn("try again", body["error"].lower())

    def test_rate_limit_message_hides_upstream_body(self):
        err = urllib.error.HTTPError("u", 429, "Too Many", {}, None)
        with mock.patch("server.groq_generate_questions", side_effect=err):
            res, body = self.request("POST", "/api/generate-test", {"job_title": "dev"})
        self.assertEqual(res.status, 502)
        self.assertIn("rate limiting", body["error"])

    def test_score_ranks_jobs_and_survives_adzuna_outage(self):
        answers = [{"question": "Q", "chosen_option": "A"}]
        with mock.patch("server.groq_score_answers", return_value=FAKE_QUAL), \
             mock.patch("server.adzuna_search", return_value=FAKE_JOBS):
            res, body = self.request("POST", "/api/score",
                                     {"job_title": "dev", "answers": answers})
        self.assertEqual(res.status, 200)
        scores = [j["match_score"] for j in body["jobs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIsNone(body["warning"])

        server.ADZUNA_CACHE = server.TTLCache(600, 200)
        with mock.patch("server.groq_score_answers", return_value=FAKE_QUAL), \
             mock.patch("server.adzuna_search", side_effect=urllib.error.URLError("down")):
            res, body = self.request("POST", "/api/score",
                                     {"job_title": "dev", "answers": answers})
        self.assertEqual(res.status, 200)
        self.assertEqual(body["jobs"], [])
        self.assertTrue(body["warning"])

    def test_score_requires_answers(self):
        res, _ = self.request("POST", "/api/score", {"job_title": "dev", "answers": []})
        self.assertEqual(res.status, 400)

    def test_unknown_route_404(self):
        res, _ = self.request("POST", "/api/nope", {})
        self.assertEqual(res.status, 404)


if __name__ == "__main__":
    unittest.main()

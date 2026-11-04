"""Tests against a fake Oura API (no network needed).

    python3 -m unittest discover -s tests
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demo_data  # noqa: E402
import server  # noqa: E402
from oura_api import USER_AGENT, NotAuthenticated, OuraClient, OuraError, TokenStore, sandbox_client  # noqa: E402


class FakeOura:
    """A tiny stand-in for api.ouraring.com."""

    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()
        self.valid_tokens = {"access-1"}
        self.refresh_tokens = {"refresh-1": ("access-2", "refresh-2")}
        self.token_calls = []
        self.fail = {}  # path -> [(status, body, headers), ...], each served once
        self.pages = {}  # path -> list of page dicts
        self.revoked = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, payload, headers=None):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
                with fake.lock:
                    fake.token_calls.append(form)
                if form.get("client_id") != "cid" or form.get("client_secret") != "secret":
                    return self._reply(401, {"error": "invalid_client"})
                if form.get("grant_type") == "authorization_code":
                    if form.get("code") != "good-code":
                        return self._reply(400, {"error": "invalid_grant", "error_description": "bad code"})
                    fake.last_verifier = form.get("code_verifier")
                    return self._reply(200, {"token_type": "bearer", "access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 86400})
                if form.get("grant_type") == "refresh_token":
                    time.sleep(0.05)  # widen the race window for the concurrency test
                    with fake.lock:
                        pair = fake.refresh_tokens.pop(form.get("refresh_token"), None)
                        if pair:
                            fake.valid_tokens = {pair[0]}
                    if not pair:
                        return self._reply(400, {"error": "invalid_grant", "error_description": "Token Already Used Or Revoked"})
                    return self._reply(200, {"token_type": "bearer", "access_token": pair[0], "refresh_token": pair[1], "expires_in": 86400})
                return self._reply(400, {"error": "unsupported_grant_type"})

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                if url.path == "/oauth/revoke":
                    fake.revoked.append(query.get("access_token"))
                    return self._reply(200, {})
                with fake.lock:
                    fake.requests.append({"path": url.path, "query": query, "headers": dict(self.headers)})
                    failures = fake.fail.get(url.path)
                    failure = failures.pop(0) if failures else None
                auth = self.headers.get("Authorization", "")
                if auth.removeprefix("Bearer ") not in fake.valid_tokens:
                    return self._reply(401, {"status": 401, "title": "Invalid Access Token"})
                if failure:
                    return self._reply(*failure)
                pages = fake.pages.get(url.path)
                if pages is None:
                    if url.path.endswith("/personal_info"):
                        return self._reply(200, {"id": "u1", "age": 40})
                    return self._reply(200, {"data": [], "next_token": None})
                idx = int(query.get("next_token", "0"))
                nxt = str(idx + 1) if idx + 1 < len(pages) else None
                return self._reply(200, {"data": pages[idx], "next_token": nxt})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOura()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TokenStore(Path(self.tmp.name) / "tokens.json")
        self.sleeps = []
        self.client = OuraClient(
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://localhost:8765/callback",
            token_store=self.store,
            api_base=self.fake.base,
            authorize_url="https://cloud.example/oauth/authorize",
            token_url=f"{self.fake.base}/oauth/token",
            revoke_url=f"{self.fake.base}/oauth/revoke",
            sleep=self.sleeps.append,
        )

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def login(self):
        url = self.client.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        self.client.handle_callback("good-code", state)


class OAuthTests(ClientTestCase):
    def test_authorization_url_has_pkce_and_scopes(self):
        url = self.client.authorization_url()
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(parts.query)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://cloud.example/oauth/authorize")
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["client_id"], ["cid"])
        self.assertEqual(q["redirect_uri"], ["http://localhost:8765/callback"])
        self.assertIn("daily", q["scope"][0].split())
        self.assertEqual(q["code_challenge_method"], ["S256"])
        verifier = self.client._pending[q["state"][0]][0]
        self.assertTrue(43 <= len(verifier) <= 128)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(q["code_challenge"], [expected])

    def test_callback_exchanges_code_and_saves_tokens_privately(self):
        url = self.client.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        verifier = self.client._pending[state][0]
        self.client.handle_callback("good-code", state)
        form = self.fake.token_calls[-1]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["redirect_uri"], "http://localhost:8765/callback")
        self.assertEqual(form["code_verifier"], verifier)
        saved = self.store.load()
        self.assertEqual(saved["access_token"], "access-1")
        self.assertEqual(saved["refresh_token"], "refresh-1")
        self.assertGreater(saved["expires_at"], time.time())
        if os.name == "posix":
            self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)

    def test_callback_rejects_unknown_state(self):
        self.client.authorization_url()
        with self.assertRaises(OuraError):
            self.client.handle_callback("good-code", "forged-state")
        self.assertIsNone(self.store.load())

    def test_state_is_single_use(self):
        url = self.client.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        self.client.handle_callback("good-code", state)
        with self.assertRaises(OuraError):
            self.client.handle_callback("good-code", state)

    def test_access_denied_is_reported(self):
        with self.assertRaisesRegex(OuraError, "許可されませんでした"):
            self.client.handle_callback(None, None, "access_denied")

    def test_bad_code_surfaces_oura_message(self):
        url = self.client.authorization_url()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        with self.assertRaisesRegex(OuraError, "bad code"):
            self.client.handle_callback("wrong", state)


class DataTests(ClientTestCase):
    def test_not_logged_in(self):
        with self.assertRaises(NotAuthenticated):
            self.client.get("/v2/usercollection/daily_sleep")

    def test_pagination_and_headers(self):
        self.login()
        path = "/v2/usercollection/daily_sleep"
        self.fake.pages[path] = [[{"day": "2026-11-01"}], [{"day": "2026-11-02"}], [{"day": "2026-11-03"}]]
        items = self.client.get_all(path, {"start_date": "2026-11-01", "end_date": "2026-11-04"})
        self.assertEqual([i["day"] for i in items], ["2026-11-01", "2026-11-02", "2026-11-03"])
        reqs = [r for r in self.fake.requests if r["path"] == path]
        self.assertEqual(len(reqs), 3)
        self.assertEqual(reqs[0]["query"], {"start_date": "2026-11-01", "end_date": "2026-11-04"})
        self.assertEqual(reqs[2]["query"]["next_token"], "2")
        self.assertEqual(reqs[0]["headers"]["Authorization"], "Bearer access-1")
        self.assertEqual(reqs[0]["headers"]["User-Agent"], USER_AGENT)

    def test_401_refreshes_once_and_retries(self):
        self.login()
        self.fake.valid_tokens = {"access-2"}  # access-1 was revoked server-side
        page = self.client.get("/v2/usercollection/daily_sleep")
        self.assertEqual(page["data"], [])
        saved = self.store.load()
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("access-2", "refresh-2"))
        self.assertEqual(self.fake.token_calls[-1]["refresh_token"], "refresh-1")

    def test_expired_token_is_refreshed_before_the_request(self):
        self.login()
        tokens = self.store.load()
        tokens["expires_at"] = time.time() - 10
        self.store.save(tokens)
        self.client.get("/v2/usercollection/daily_sleep")
        self.assertEqual(self.store.load()["access_token"], "access-2")
        self.assertEqual([r["headers"]["Authorization"] for r in self.fake.requests], ["Bearer access-2"])

    def test_revoked_refresh_token_logs_out(self):
        self.login()
        self.fake.valid_tokens = set()
        self.fake.refresh_tokens = {}
        with self.assertRaises(NotAuthenticated):
            self.client.get("/v2/usercollection/daily_sleep")
        self.assertIsNone(self.store.load())

    def test_concurrent_401s_refresh_only_once(self):
        self.login()
        self.fake.valid_tokens = {"access-2"}
        errors = []

        def worker():
            try:
                self.client.get("/v2/usercollection/daily_sleep")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        refreshes = [c for c in self.fake.token_calls if c["grant_type"] == "refresh_token"]
        self.assertEqual(len(refreshes), 1)

    def test_429_waits_for_retry_after(self):
        self.login()
        path = "/v2/usercollection/daily_activity"
        self.fake.fail[path] = [(429, {"status": 429, "title": "Request Rate Limit Exceeded"}, {"Retry-After": "7"})]
        self.client.get(path)
        self.assertEqual(self.sleeps, [7])

    def test_other_errors_raise_with_detail(self):
        self.login()
        path = "/v2/usercollection/daily_spo2"
        self.fake.fail[path] = [(403, {"status": 403, "title": "Missing Scopes", "detail": "spo2 scope required"}, {})]
        with self.assertRaisesRegex(OuraError, "spo2 scope required") as ctx:
            self.client.get(path)
        self.assertEqual(ctx.exception.status, 403)


class LiveSourceTests(ClientTestCase):
    def test_dashboard_collects_everything_and_keeps_partial_failures(self):
        self.login()
        sleep_doc = {
            "id": "s1", "day": "2026-11-03", "type": "long_sleep", "total_sleep_duration": 25000,
            "bedtime_end": "2026-11-03T07:00:00+09:00", "sleep_phase_5_min": "4221",
            "sleep_phase_30_sec": "x" * 900, "movement_30_sec": "1" * 900,
            "heart_rate": {"interval": 300, "items": [60, 58], "timestamp": "2026-11-02T23:00:00+09:00"},
            "hrv": {"interval": 300, "items": [40, 42], "timestamp": "2026-11-02T23:00:00+09:00"},
        }
        older = {**sleep_doc, "id": "s0", "day": "2026-11-02", "bedtime_end": "2026-11-02T07:00:00+09:00"}
        self.fake.pages["/v2/usercollection/sleep"] = [[older, sleep_doc]]
        self.fake.pages["/v2/usercollection/daily_readiness"] = [[{"day": "2026-11-03", "score": 80}]]
        self.fake.fail["/v2/usercollection/daily_spo2"] = [(403, {"status": 403, "title": "Missing Scopes"}, {})]
        source = server.LiveSource(self.client)
        payload = source.dashboard(dt.date(2026, 11, 1), dt.date(2026, 11, 3))

        self.assertEqual(payload["data"]["daily_readiness"], [{"day": "2026-11-03", "score": 80}])
        self.assertEqual(payload["data"]["daily_spo2"], [])
        self.assertIn("daily_spo2", payload["errors"])
        self.assertEqual(payload["personal_info"], {"id": "u1", "age": 40})
        sleeps = {s["id"]: s for s in payload["data"]["sleep"]}
        self.assertNotIn("sleep_phase_30_sec", sleeps["s1"])
        self.assertIn("heart_rate", sleeps["s1"])  # latest night keeps its samples
        self.assertNotIn("heart_rate", sleeps["s0"])
        daily = [r for r in self.fake.requests if r["path"] == "/v2/usercollection/daily_sleep"][0]
        self.assertEqual(daily["query"], {"start_date": "2026-11-01", "end_date": "2026-11-04"})
        hr = [r for r in self.fake.requests if r["path"] == "/v2/usercollection/heartrate"][0]
        self.assertIn("start_datetime", hr["query"])

        # Second call is served from the cache, except the collection that failed;
        # refresh=True goes back to Oura for everything.
        count = len(self.fake.requests)
        source.dashboard(dt.date(2026, 11, 1), dt.date(2026, 11, 3))
        self.assertEqual([r["path"] for r in self.fake.requests[count:]], ["/v2/usercollection/daily_spo2"])
        count = len(self.fake.requests)
        source.dashboard(dt.date(2026, 11, 1), dt.date(2026, 11, 3), refresh=True)
        self.assertGreater(len(self.fake.requests), count)

    def test_slim_sleep_keeps_samples_for_the_latest_drawable_night(self):
        hr = {"interval": 300, "items": [60], "timestamp": "x"}
        drawable = {"id": "a", "day": "2026-11-02", "type": "long_sleep", "bedtime_end": "2026-11-02T07:00", "app_sleep_phase_5_min": "42", "sleep_phase_5_min": None, "heart_rate": hr}
        newer = {"id": "b", "day": "2026-11-03", "type": "long_sleep", "bedtime_end": "2026-11-03T07:00", "sleep_phase_5_min": None, "heart_rate": hr}
        out = {s["id"]: s for s in server.slim_sleep([drawable, newer])}
        self.assertIn("heart_rate", out["a"])
        self.assertNotIn("heart_rate", out["b"])


class SandboxTests(ClientTestCase):
    def test_sandbox_reads_sandbox_paths_without_login(self):
        self.fake.valid_tokens = {"sandbox"}
        self.fake.pages["/v2/sandbox/usercollection/daily_sleep"] = [[{"day": "2026-11-03", "score": 70}]]
        source = server.LiveSource(sandbox_client(self.fake.base), sandbox=True)
        self.assertEqual(source.status()["mode"], "sandbox")
        self.assertTrue(source.status()["authenticated"])
        payload = source.dashboard(dt.date(2026, 11, 1), dt.date(2026, 11, 3))
        self.assertEqual(payload["source"], "sandbox")
        self.assertEqual(payload["data"]["daily_sleep"], [{"day": "2026-11-03", "score": 70}])
        self.assertEqual(payload["errors"], {})
        paths = {r["path"] for r in self.fake.requests}
        self.assertTrue(all(p.startswith("/v2/sandbox/usercollection/") for p in paths), paths)
        self.assertNotIn("/v2/sandbox/usercollection/personal_info", paths)
        self.assertEqual({r["headers"]["Authorization"] for r in self.fake.requests}, {"Bearer sandbox"})

    def test_login_is_refused_in_sandbox_mode(self):
        source = server.LiveSource(sandbox_client(self.fake.base), sandbox=True)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(source, 0))
        port = httpd.server_address[1]
        httpd.RequestHandlerClass = server.make_handler(source, port)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/login", headers={"Host": f"localhost:{port}"})
            self.assertEqual(conn.getresponse().status, 400)
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()


@unittest.skipUnless(os.environ.get("OURA_SANDBOX_TEST") == "1", "set OURA_SANDBOX_TEST=1 to call the real Oura sandbox")
class RealSandboxTests(unittest.TestCase):
    """Calls https://api.ouraring.com/v2/sandbox/... (needs internet, no account)."""

    def test_real_sandbox_shapes(self):
        source = server.LiveSource(sandbox_client(), sandbox=True)
        end = dt.date.today()
        payload = source.dashboard(end - dt.timedelta(days=6), end)
        self.assertEqual(payload["errors"], {})
        data = payload["data"]
        for name in ("daily_readiness", "daily_sleep", "daily_activity", "sleep"):
            self.assertTrue(data[name], name)
        r = data["daily_readiness"][0]
        self.assertTrue({"day", "score", "contributors"} <= set(r))
        s = data["sleep"][0]
        self.assertTrue({"day", "type", "bedtime_start", "bedtime_end", "total_sleep_duration"} <= set(s))
        self.assertTrue(any(x.get("app_sleep_phase_5_min") or x.get("sleep_phase_5_min") for x in data["sleep"]))


class HttpTests(ClientTestCase):
    def start(self, source):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(source, 0))
        self.port = self.httpd.server_address[1]
        # make_handler checks Host against the configured port; rebuild with the real one.
        self.httpd.RequestHandlerClass = server.make_handler(source, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, headers={"Host": host or f"localhost:{self.port}"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_login_callback_roundtrip(self):
        self.start(server.LiveSource(self.client))
        resp, body = self.request("GET", "/api/status")
        self.assertEqual(json.loads(body)["authenticated"], False)
        resp, _ = self.request("GET", "/api/dashboard")
        self.assertEqual(resp.status, 401)

        resp, _ = self.request("GET", "/login")
        self.assertEqual(resp.status, 302)
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(resp.getheader("Location")).query)["state"][0]
        resp, _ = self.request("GET", f"/callback?code=good-code&state={state}")
        self.assertEqual((resp.status, resp.getheader("Location")), (302, "/"))
        self.assertEqual(self.store.load()["access_token"], "access-1")

        resp, body = self.request("GET", "/api/dashboard?start=2026-11-01&end=2026-11-03")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(body)["source"], "live")

        resp, _ = self.request("POST", "/logout")
        self.assertEqual(resp.status, 200)
        self.assertIsNone(self.store.load())
        self.assertEqual(self.fake.revoked, ["access-1"])

    def test_rejects_foreign_host_header(self):
        self.start(server.DemoSource())
        resp, _ = self.request("GET", "/api/status", host="attacker.example")
        self.assertEqual(resp.status, 403)

    def test_bad_dates(self):
        self.start(server.DemoSource())
        for q in ("start=2026-11-05&end=2026-11-01", "start=nope&end=2026-11-01", "start=2020-01-01&end=2026-11-01"):
            resp, body = self.request("GET", f"/api/dashboard?{q}")
            self.assertEqual(resp.status, 400, q)

    def test_static_files_cannot_escape(self):
        self.start(server.DemoSource())
        self.assertEqual(self.request("GET", "/")[0].status, 200)
        self.assertEqual(self.request("GET", "/static/app.js")[0].status, 200)
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fserver.py"):
            self.assertEqual(self.request("GET", path)[0].status, 404, path)

    def test_callback_errors_are_escaped(self):
        self.start(server.LiveSource(self.client))
        resp, body = self.request("GET", "/callback?error=%3Cscript%3E")
        self.assertEqual(resp.status, 400)
        self.assertNotIn(b"<script>", body)


class DemoAndHelpersTests(unittest.TestCase):
    def test_demo_data_matches_api_shape(self):
        g = demo_data.generate(dt.date(2026, 10, 1), dt.date(2026, 10, 30), now=dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc))
        d = g["data"]
        self.assertEqual(len(d["daily_readiness"]), 30)
        r = d["daily_readiness"][0]
        self.assertTrue({"day", "score", "contributors", "temperature_deviation"} <= set(r))
        s = next(x for x in d["sleep"] if x["type"] == "long_sleep")
        self.assertEqual(len(s["sleep_phase_5_min"]) * 300, s["time_in_bed"])
        self.assertEqual(s["deep_sleep_duration"] + s["light_sleep_duration"] + s["rem_sleep_duration"], s["total_sleep_duration"])
        self.assertEqual(len(s["heart_rate"]["items"]), len(s["sleep_phase_5_min"]))
        self.assertTrue(all(0 <= x["score"] <= 100 for x in d["daily_sleep"]))
        self.assertGreater(len(g["heartrate"]), 200)
        # Same day, same values, whatever the range.
        g2 = demo_data.generate(dt.date(2026, 10, 15), dt.date(2026, 10, 30), now=dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc))
        self.assertEqual(g2["data"]["daily_sleep"][-1], d["daily_sleep"][-1])

    def test_main_sleep_prefers_long_sleep(self):
        sleeps = [
            {"id": "nap", "day": "2026-11-01", "type": "late_nap", "total_sleep_duration": 30000},
            {"id": "night", "day": "2026-11-01", "type": "long_sleep", "total_sleep_duration": 20000},
            {"id": "gone", "day": "2026-11-02", "type": "deleted", "total_sleep_duration": 20000},
        ]
        mains = server.main_sleep_per_day(sleeps)
        self.assertEqual(mains["2026-11-01"]["id"], "night")
        self.assertNotIn("2026-11-02", mains)

    def test_env_file_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('# comment\nOURA_CLIENT_ID=abc\nexport OURA_CLIENT_SECRET="s e c"\nBROKEN\n', encoding="utf-8")
            self.assertEqual(server.load_env_file(p), {"OURA_CLIENT_ID": "abc", "OURA_CLIENT_SECRET": "s e c"})
            self.assertEqual(server.load_env_file(Path(tmp) / "missing"), {})


if __name__ == "__main__":
    unittest.main()

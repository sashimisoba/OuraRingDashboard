#!/usr/bin/env python3
"""Personal Oura Ring dashboard - local web server.

    python3 server.py           # your own data (needs .env, see README.md)
    python3 server.py --demo    # sample data, no Oura account needed
    python3 server.py --sandbox # Oura's sandbox data via the real API, no login

Only the Python standard library is used. The server listens on 127.0.0.1, so
the dashboard (and your Oura token) is reachable from this computer only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import mimetypes
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import demo_data
from oura_api import (
    DEFAULT_SCOPES,
    SANDBOX_PREFIX,
    USER_PREFIX,
    NotAuthenticated,
    OuraClient,
    OuraError,
    TokenStore,
    sandbox_client,
)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_PORT = 8765
CACHE_TTL_SECONDS = 10 * 60
MAX_RANGE_DAYS = 400

# Collections read with start_date / end_date. Each one is optional: a missing
# scope or an older ring only blanks the matching card.
DAILY_COLLECTIONS = [
    "daily_readiness",
    "daily_sleep",
    "daily_activity",
    "sleep",
    "daily_stress",
    "daily_spo2",
    "daily_resilience",
    "daily_cardiovascular_age",
    "vO2_max",
    "workout",
]

# Large per-night strings the dashboard never draws. (Both 5-minute hypnogram
# strings are kept: real data sometimes fills only app_sleep_phase_5_min.)
SLEEP_DROP_FIELDS = ("sleep_phase_30_sec", "movement_30_sec")


def load_env_file(path: Path) -> dict:
    """Read KEY=VALUE lines from a .env file (no shell expansion)."""
    values: dict = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main_sleep_per_day(sleeps: list) -> dict:
    """The night's main sleep for each day (long_sleep first, then the longest)."""
    best: dict = {}
    for s in sleeps:
        if s.get("type") == "deleted" or not s.get("day"):
            continue
        rank = (s.get("type") == "long_sleep", s.get("total_sleep_duration") or 0)
        if s["day"] not in best or rank > best[s["day"]][0]:
            best[s["day"]] = (rank, s)
    return {day: s for day, (_, s) in best.items()}


def slim_sleep(sleeps: list) -> list:
    """Drop heavy fields; keep heart rate / HRV samples only for the night the dashboard draws.

    That is the latest main sleep that has a hypnogram (the same rule as app.js).
    """
    mains = list(main_sleep_per_day(sleeps).values())
    drawable = [s for s in mains if s.get("app_sleep_phase_5_min") or s.get("sleep_phase_5_min")]
    latest = max(drawable or mains, key=lambda s: s.get("bedtime_end") or "", default=None)
    out = []
    for s in sleeps:
        s = {k: v for k, v in s.items() if k not in SLEEP_DROP_FIELDS}
        if latest is None or s.get("id") != latest.get("id"):
            s.pop("heart_rate", None)
            s.pop("hrv", None)
        out.append(s)
    return out


def parse_day(value: str | None, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value or "")
    except ValueError:
        raise ValueError(f"{name} は YYYY-MM-DD 形式で指定してください") from None


# ---- data sources --------------------------------------------------------------


class LiveSource:
    """Reads your data from the Oura API, with a short in-memory cache.

    With sandbox=True it reads Oura's sandbox collections instead: fixed sample
    data served by the real API, no login needed.
    """

    def __init__(self, client: OuraClient | None, config_problem: str | None = None, sandbox: bool = False):
        self.client = client
        self.config_problem = config_problem
        self.mode = "sandbox" if sandbox else "live"
        self.prefix = SANDBOX_PREFIX if sandbox else USER_PREFIX
        self._cache: dict = {}
        self._cache_lock = threading.Lock()

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "configured": self.client is not None,
            "config_problem": self.config_problem,
            "authenticated": bool(self.client and self.client.is_authenticated()),
            "redirect_uri": (self.client.redirect_uri or None) if self.client else None,
        }

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _cached(self, key, fetch):
        now = time.time()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
        value = fetch()
        with self._cache_lock:
            self._cache[key] = (now, value)
        return value

    def dashboard(self, start: dt.date, end: dt.date, refresh: bool = False) -> dict:
        if self.client is None:
            raise NotAuthenticated(self.config_problem or "Oura API の設定がありません")
        if refresh:
            self.clear_cache()
        client = self.client
        # Some collections treat end_date as exclusive, so ask for one extra day.
        params = {"start_date": start.isoformat(), "end_date": (end + dt.timedelta(days=1)).isoformat()}
        now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        hr_params = {
            "start_datetime": (now - dt.timedelta(hours=24)).isoformat(),
            "end_datetime": now.isoformat(),
        }

        jobs = {name: (f"{self.prefix}/{name}", params, True) for name in DAILY_COLLECTIONS}
        jobs["heartrate"] = (f"{self.prefix}/heartrate", hr_params, True)
        if self.mode == "live":  # the sandbox has no personal_info
            jobs["personal_info"] = (f"{self.prefix}/personal_info", None, False)

        def run(name):
            path, p, collection = jobs[name]
            # Heart rate is cached per 10-minute slot so the "now" window stays stable.
            key = (name, json.dumps(p, sort_keys=True) if name != "heartrate" else int(time.time() // CACHE_TTL_SECONDS))
            return self._cached(key, lambda: client.get_all(path, p) if collection else client.get(path, p))

        results: dict = {}
        errors: dict = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {name: pool.submit(run, name) for name in jobs}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result()
                except NotAuthenticated:
                    raise
                except OuraError as e:
                    errors[name] = str(e)
                    results[name] = None if name == "personal_info" else []

        personal = results.pop("personal_info", None)
        heartrate = results.pop("heartrate")
        results["sleep"] = slim_sleep(results.get("sleep") or [])
        return {
            "source": self.mode,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "personal_info": personal,
            "data": results,
            "heartrate": heartrate,
            "errors": errors,
        }


class DemoSource:
    """Generated sample data in the same shape as the Oura API."""

    mode = "demo"

    def status(self) -> dict:
        return {"mode": self.mode, "configured": True, "config_problem": None, "authenticated": True, "redirect_uri": None}

    def dashboard(self, start: dt.date, end: dt.date, refresh: bool = False) -> dict:
        generated = demo_data.generate(start, end)
        generated["data"]["sleep"] = slim_sleep(generated["data"]["sleep"])
        return {
            "source": self.mode,
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "fetched_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            **generated,
            "errors": {},
        }


# ---- HTTP ----------------------------------------------------------------------

MESSAGE_PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oura ダッシュボード</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP",sans-serif;background:#f9f9f7;color:#0b0b0b;margin:0;padding:48px 16px}}
main{{max-width:560px;margin:0 auto;background:#fcfcfb;border:1px solid rgba(11,11,11,.1);border-radius:12px;padding:24px}}
a{{color:#256abf}}
@media (prefers-color-scheme:dark){{body{{background:#0d0d0d;color:#f0efec}}main{{background:#1a1a19;border-color:rgba(255,255,255,.1)}}a{{color:#86b6ef}}}}
</style></head>
<body><main><h1 style="font-size:20px">{title}</h1><p>{body}</p><p><a href="/">ダッシュボードに戻る</a></p></main></body></html>"""


def html_escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def make_handler(source, port: int):
    allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "OuraDashboard/1.0"

        def log_message(self, fmt, *args):
            # Never log query strings: the OAuth callback carries the auth code.
            path = self.path.split("?", 1)[0]
            sys.stderr.write(f"{self.log_date_time_string()} {self.command} {path} {args[1] if len(args) > 1 else ''}\n")

        # -- helpers --
        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding pages from reading the local API.
            return self.headers.get("Host", "") in allowed_hosts

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _page(self, status: int, title: str, body_html: str):
            html = MESSAGE_PAGE.format(title=html_escape(title), body=body_html)
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8")

        def _redirect(self, location: str):
            self._send(302, b"", "text/plain", {"Location": location})

        def _static(self, rel: str):
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        # -- routes --
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"Invalid Host header", "text/plain; charset=utf-8")
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            q = lambda name: (query.get(name) or [None])[0]  # noqa: E731

            if url.path in ("/", "/index.html"):
                return self._static("index.html")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path == "/api/status":
                return self._json(200, source.status())
            if url.path == "/api/dashboard":
                return self._dashboard(q)
            if url.path == "/login":
                return self._login()
            if url.path == "/callback":
                return self._callback(q)
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"Invalid Host header", "text/plain; charset=utf-8")
            if urllib.parse.urlsplit(self.path).path == "/logout":
                if isinstance(source, LiveSource) and source.mode == "live" and source.client:
                    source.client.logout()
                    source.clear_cache()
                return self._json(200, {"ok": True})
            return self._send(404, b"not found", "text/plain; charset=utf-8")

        def _dashboard(self, q):
            try:
                end = parse_day(q("end") or dt.date.today().isoformat(), "end")
                start = parse_day(q("start") or (end - dt.timedelta(days=29)).isoformat(), "start")
                if start > end:
                    raise ValueError("start は end 以前の日付にしてください")
                if (end - start).days >= MAX_RANGE_DAYS:
                    raise ValueError(f"期間は {MAX_RANGE_DAYS} 日以内にしてください")
            except ValueError as e:
                return self._json(400, {"error": "bad_request", "message": str(e)})
            try:
                payload = source.dashboard(start, end, refresh=q("refresh") == "1")
            except NotAuthenticated as e:
                return self._json(401, {"error": "not_authenticated", "message": e.message})
            except OuraError as e:
                return self._json(502, {"error": "oura_error", "message": str(e)})
            return self._json(200, payload)

        def _login(self):
            if not isinstance(source, LiveSource) or source.mode != "live" or source.client is None:
                problem = getattr(source, "config_problem", None) or "デモ / Sandbox モードではログインできません"
                return self._page(400, "ログインできません", html_escape(problem))
            return self._redirect(source.client.authorization_url())

        def _callback(self, q):
            if not isinstance(source, LiveSource) or source.mode != "live" or source.client is None:
                return self._page(400, "ログインできません", "Oura API の設定がありません。")
            try:
                source.client.handle_callback(q("code"), q("state"), q("error"))
            except OuraError as e:
                return self._page(400, "ログインに失敗しました", html_escape(str(e)))
            source.clear_cache()
            return self._redirect("/")

    return Handler


def build_source(args, env: dict):
    if args.demo:
        return DemoSource()
    if args.sandbox:
        return LiveSource(sandbox_client(env.get("OURA_API_BASE", "").strip() or "https://api.ouraring.com"), sandbox=True)
    client_id = env.get("OURA_CLIENT_ID", "").strip()
    client_secret = env.get("OURA_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return LiveSource(None, "OURA_CLIENT_ID と OURA_CLIENT_SECRET が .env に設定されていません")
    redirect_uri = env.get("OURA_REDIRECT_URI", "").strip() or f"http://localhost:{args.port}/callback"
    token_file = Path(env.get("OURA_TOKEN_FILE", "").strip() or ROOT / ".oura_tokens.json")
    client = OuraClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        token_store=TokenStore(token_file),
        scopes=env.get("OURA_SCOPES", "").strip() or DEFAULT_SCOPES,
        api_base=env.get("OURA_API_BASE", "").strip() or "https://api.ouraring.com",
        **({"authorize_url": env["OURA_AUTHORIZE_URL"]} if env.get("OURA_AUTHORIZE_URL") else {}),
        **({"token_url": env["OURA_TOKEN_URL"]} if env.get("OURA_TOKEN_URL") else {}),
    )
    return LiveSource(client)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Oura Ring の個人用ダッシュボード")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="手元で作ったサンプルデータで起動する (Oura アカウント不要)")
    mode.add_argument("--sandbox", action="store_true", help="Oura API の Sandbox (テスト用データ) を読む (ログイン不要)")
    parser.add_argument("--port", type=int, default=None, help=f"ポート番号 (既定 {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="起動時にブラウザを開かない")
    args = parser.parse_args(argv)

    env = {**load_env_file(ROOT / ".env"), **os.environ}
    if args.port is None:
        args.port = int(env.get("OURA_PORT") or DEFAULT_PORT)
    source = build_source(args, env)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(source, args.port))
    url = f"http://localhost:{args.port}/"
    label = "デモデータ" if args.demo else "Oura API Sandbox" if args.sandbox else "Oura API"
    print(f"Oura ダッシュボード: {url}  ({label})")
    if isinstance(source, LiveSource) and source.config_problem:
        print(f"  注意: {source.config_problem}。README.md の手順を参照してください。")
    print("  終了するには Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

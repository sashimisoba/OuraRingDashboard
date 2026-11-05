"""Oura API v2 client.

Handles the OAuth2 authorization-code flow (with PKCE), stores tokens on disk,
refreshes them when they expire, follows `next_token` pagination and backs off
on 429 responses.

Personal Access Tokens were retired by Oura in December 2025, so OAuth2 is the
only way to read your own data.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
REVOKE_URL = "https://api.ouraring.com/oauth/revoke"
API_BASE = "https://api.ouraring.com"

# Oura rejects requests without a User-Agent, and urllib's default is not
# guaranteed to be accepted.
USER_AGENT = "OuraPersonalDashboard/1.0"

# Empty = leave `scope` out of the login request. Oura then asks for every scope
# the app was registered with, so the scopes ticked on the app page are used as-is.
DEFAULT_SCOPES = ""

# Refresh a little before the token actually expires.
EXPIRY_MARGIN_SECONDS = 120
# How long a /login attempt stays valid.
STATE_TTL_SECONDS = 600
MAX_RATE_LIMIT_RETRIES = 2
# A token this new that gets a 401 is not expired; refreshing would only burn the
# single-use refresh token, so the error is reported instead.
FRESH_TOKEN_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 30


class OuraError(Exception):
    """An error response from Oura (or a network failure)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}" if status else message)
        self.status = status
        self.message = message


class NotAuthenticated(OuraError):
    """No usable token: the user has to log in (again)."""

    def __init__(self, message: str = "Oura にログインしていません"):
        super().__init__(401, message)


def _error_message(body: bytes) -> str:
    """Pull a readable message out of an RFC 7807 / OAuth error body (title + detail)."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        text = body.decode("utf-8", "replace").strip()
        return text[:300] or "unknown error"
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)[:300]
    parts: list[str] = []
    for key in ("title", "error_description", "detail", "message", "error"):
        value = data.get(key)
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if not any(text in p or p in text for p in parts):
            parts.append(text)
    return ": ".join(parts)[:400] or json.dumps(data, ensure_ascii=False)[:300]


def _is_scope_error(message: str) -> bool:
    # "Missing Scopes" can come back as 401; a new token would not help.
    return "scope" in message.lower()


class TokenStore:
    """Keeps the OAuth tokens in a JSON file readable only by the current user."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("access_token") else None

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tokens-", suffix=".tmp")
        try:
            os.chmod(tmp, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class MemoryTokenStore:
    """Keeps a fixed token in memory (used for Oura's sandbox, which accepts any token)."""

    def __init__(self, tokens: dict | None = None):
        self._tokens = dict(tokens) if tokens else None

    def load(self) -> dict | None:
        return dict(self._tokens) if self._tokens else None

    def save(self, tokens: dict) -> None:
        self._tokens = dict(tokens)

    def clear(self) -> None:
        self._tokens = None


USER_PREFIX = "/v2/usercollection"
SANDBOX_PREFIX = "/v2/sandbox/usercollection"


def sandbox_client(api_base: str = API_BASE) -> "OuraClient":
    """A client for Oura's sandbox: fixed sample data, no login needed."""
    return OuraClient(
        client_id="",
        client_secret="",
        redirect_uri="",
        token_store=MemoryTokenStore({"access_token": "sandbox", "refresh_token": None, "expires_at": None}),
        api_base=api_base,
    )


class OuraClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_store: TokenStore,
        scopes: str = DEFAULT_SCOPES,
        api_base: str = API_BASE,
        authorize_url: str = AUTHORIZE_URL,
        token_url: str = TOKEN_URL,
        revoke_url: str = REVOKE_URL,
        sleep=time.sleep,
        clock=time.time,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.tokens = token_store
        self.api_base = api_base.rstrip("/")
        self.authorize_endpoint = authorize_url
        self.token_endpoint = token_url
        self.revoke_endpoint = revoke_url
        self._sleep = sleep
        self._clock = clock
        # Refresh tokens are single-use, so only one thread may refresh at a time.
        self._refresh_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}

    # ---- login ---------------------------------------------------------------

    def authorization_url(self) -> str:
        """Start a login: returns the Oura consent URL to redirect the browser to."""
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)  # 86 chars, inside PKCE's 43-128
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        now = self._clock()
        with self._pending_lock:
            self._pending = {s: v for s, v in self._pending.items() if now - v[1] < STATE_TTL_SECONDS}
            self._pending[state] = (verifier, now)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self.scopes:
            params["scope"] = self.scopes
        query = urllib.parse.urlencode(params)
        return f"{self.authorize_endpoint}?{query}"

    def handle_callback(self, code: str | None, state: str | None, error: str | None = None) -> None:
        """Finish a login with the values Oura sent to the redirect URI."""
        if error:
            if error == "access_denied":
                raise OuraError(0, "Oura 側でアクセスが許可されませんでした")
            raise OuraError(0, f"Oura がエラーを返しました: {error}")
        with self._pending_lock:
            entry = self._pending.pop(state or "", None)
        if not entry or self._clock() - entry[1] > STATE_TTL_SECONDS:
            raise OuraError(0, "ログインの有効期限が切れたか、不正なリクエストです。もう一度ログインしてください")
        if not code:
            raise OuraError(0, "認可コードがありません")
        verifier = entry[0]
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": verifier,
            }
        )
        self.tokens.save(tokens)

    def is_authenticated(self) -> bool:
        return self.tokens.load() is not None

    def logout(self) -> None:
        tokens = self.tokens.load()
        self.tokens.clear()
        if not tokens:
            return
        url = f"{self.revoke_endpoint}?{urllib.parse.urlencode({'access_token': tokens['access_token']})}"
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                timeout=REQUEST_TIMEOUT_SECONDS,
            ).close()
        except (urllib.error.URLError, OSError):
            pass  # The local copy is gone either way.

    # ---- tokens --------------------------------------------------------------

    def _token_request(self, form: dict, previous: dict | None = None) -> dict:
        body = urllib.parse.urlencode(
            {**form, "client_id": self.client_id, "client_secret": self.client_secret}
        ).encode()
        req = urllib.request.Request(
            self.token_endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise OuraError(e.code, _error_message(e.read())) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise OuraError(0, f"トークンを取得できませんでした: {e}") from None
        if not data.get("access_token"):
            raise OuraError(0, "トークンの応答に access_token がありません")
        expires_in = data.get("expires_in")
        return {
            "access_token": data["access_token"],
            # Oura may not send a new refresh token on every refresh.
            "refresh_token": data.get("refresh_token") or (previous or {}).get("refresh_token"),
            "expires_at": self._clock() + float(expires_in) if expires_in else None,
            "obtained_at": self._clock(),
            "scope": data.get("scope") or (previous or {}).get("scope"),
        }

    def _refresh(self, stale_access_token: str) -> dict:
        with self._refresh_lock:
            current = self.tokens.load()
            if not current:
                raise NotAuthenticated()
            if current["access_token"] != stale_access_token:
                return current  # Another thread already refreshed.
            if not current.get("refresh_token"):
                self.tokens.clear()
                raise NotAuthenticated("アクセストークンの期限が切れました。もう一度ログインしてください")
            try:
                fresh = self._token_request(
                    {"grant_type": "refresh_token", "refresh_token": current["refresh_token"]},
                    previous=current,
                )
            except OuraError as e:
                if e.status in (400, 401):
                    # The refresh token was revoked or already used.
                    self.tokens.clear()
                    raise NotAuthenticated("ログインの有効期限が切れました。もう一度ログインしてください") from None
                raise
            self.tokens.save(fresh)
            return fresh

    def _valid_tokens(self) -> dict:
        tokens = self.tokens.load()
        if not tokens:
            raise NotAuthenticated()
        expires_at = tokens.get("expires_at")
        if expires_at and self._clock() > expires_at - EXPIRY_MARGIN_SECONDS:
            tokens = self._refresh(tokens["access_token"])
        return tokens

    # ---- data ----------------------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET one page of an API path such as /v2/usercollection/daily_sleep."""
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.api_base}{path}" + (f"?{query}" if query else "")
        tokens = self._valid_tokens()
        refreshed = False
        rate_limited = 0
        while True:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {tokens['access_token']}",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read()
                message = _error_message(body)
                fresh = self._clock() - float(tokens.get("obtained_at") or 0) < FRESH_TOKEN_SECONDS
                if e.code == 401 and not refreshed and not fresh and not _is_scope_error(message):
                    refreshed = True
                    tokens = self._refresh(tokens["access_token"])
                    continue
                if e.code == 401:
                    # Reported per collection; the caller decides whether the
                    # login as a whole is broken (see LiveSource.dashboard).
                    raise OuraError(401, message) from None
                if e.code == 429 and rate_limited < MAX_RATE_LIMIT_RETRIES:
                    rate_limited += 1
                    try:
                        wait = int(e.headers.get("Retry-After", "5"))
                    except ValueError:
                        wait = 5
                    self._sleep(min(max(wait, 1), MAX_RETRY_AFTER_SECONDS))
                    continue
                raise OuraError(e.code, message) from None
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise OuraError(0, f"Oura API に接続できませんでした: {e}") from None

    def get_all(self, path: str, params: dict | None = None, max_pages: int = 50) -> list:
        """GET a collection and follow next_token until every page is read."""
        params = dict(params or {})
        items: list = []
        for _ in range(max_pages):
            page = self.get(path, params)
            items.extend(page.get("data") or [])
            token = page.get("next_token")
            if not token:
                break
            params["next_token"] = token
        return items

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deployed synthetic Stage OAuth/MCP proof; never a human or Google login.

No endpoint, actor, container, credential, or code override is accepted. The
five-minute root session exists only in the existing isolated Axxium Stage DB.
Credentials stay in memory and cleanup is attempted even after an uncertain
session-creation result. Call smoke() from the serialized Services controller.
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
import json
import re
import secrets
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler,
                            Request, build_opener)
import uuid

GATEWAY = "http://127.0.0.1:18771"
ISSUER = "https://staging.axxium.promethean.rest/oidc"
ORIGIN = "https://staging.axxium.promethean.rest"
CALLBACK = "http://127.0.0.1:18776/callback"
CONTAINER = "axxium-staging-axxium-1"
ACTOR = "actor_20603b93-2c96-4ff2-a213-0b1e84017993"
LIMIT = 1024 * 1024
_assertions = ContextVar("colab_stage_assertions", default=None)


class SmokeFailure(RuntimeError):
    """Safe to report; never attach raw HTTP, process, or credential data."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SmokeFailure(reason)
    count = _assertions.get()
    if count is not None:
        _assertions.set(count + 1)


def stage_url(url: str) -> bool:
    """Only the two compiled Stage peers, with no credential-bearing URLs."""
    part = urlsplit(url)
    if part.username or part.password or part.fragment or "%" in part.path:
        return False
    origin = f"{part.scheme}://{part.netloc}"
    if any(piece in {".", ".."} for piece in part.path.split("/")):
        return False
    return ((origin == ORIGIN and part.path.startswith("/oidc/")) or
            (origin == GATEWAY and part.path in {
                "/register", "/authorize", "/consent", "/auth/callback",
                "/token", "/revoke", "/mcp"}))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    def json(self):
        return json.loads(self.body)


class Browser:
    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPCookieProcessor(self.cookies))
        self.deadline = time.monotonic() + 180

    def root_cookie(self, name: str, token: str):
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name)),
                "Stage cookie contract unavailable")
        self.cookies.set_cookie(Cookie(
            0, name, token, None, False, "staging.axxium.promethean.rest", False,
            False, "/", True, True, int(time.time()) + 300, False, None, None, {}))

    def request(self, url, *, form=None, data=None, headers=None, method=None):
        require(stage_url(url), "Stage request escaped fixed peers")
        require(time.monotonic() < self.deadline, "Stage proof deadline expired")
        head = dict(headers or {})
        require(form is None or data is None, "Ambiguous Stage request")
        if form is not None:
            data = urlencode(form).encode()
            head["Content-Type"] = "application/x-www-form-urlencoded"
        elif isinstance(data, dict):
            data = json.dumps(data).encode()
            head["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=head, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=10)
            except HTTPError as response_error:
                response = response_error
            with response:
                body = response.read(LIMIT + 1)
                require(len(body) <= LIMIT, "Stage response exceeded bound")
                return Response(response.status,
                                {k.lower(): v for k, v in response.headers.items()}, body)
        except SmokeFailure:
            raise
        except Exception:
            raise SmokeFailure("Stage HTTP request failed") from None


class ConsentForm(HTMLParser):
    def __init__(self, body: bytes):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.feed(body.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "form":
            require(self.current is None, "Nested Stage consent form")
            self.current = {"method": fields.get("method", "get").lower(),
                            "action": fields.get("action", ""), "fields": {}}
        elif tag == "input" and self.current is not None:
            require(fields.get("type") == "hidden", "Unexpected Stage credential form")
            name = fields.get("name")
            require(name and name not in self.current["fields"], "Ambiguous Stage consent input")
            self.current["fields"][name] = fields.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def consent_request(url: str, body: bytes):
    parsed = ConsentForm(body)
    require(len(parsed.forms) == 1 and parsed.current is None,
            "Stage consent form contract unavailable")
    form = parsed.forms[0]
    require(form["method"] == "post", "Unexpected Stage consent method")
    target = urljoin(url, form["action"])
    part = urlsplit(target)
    fields = form["fields"]
    if part.path == "/consent" and target == url:
        require(target.startswith(GATEWAY + "/") and
                set(fields) == {"txn_id", "csrf_token", "submit"} and
                fields["submit"] == "true", "Gateway consent contract unavailable")
        fields = {**fields, "action": "approve"}
        origin = GATEWAY
    else:
        require(target == url and target.startswith(ISSUER + "/interaction/") and
                set(fields) == {"csrf"} and
                bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", fields["csrf"])),
                "Axxium consent contract unavailable")
        fields = {**fields, "decision": "approve"}
        origin = ORIGIN
    return target, fields, {"Origin": origin}


def authorization_code(browser: Browser, url: str, state: str) -> str:
    response = browser.request(url)
    for _ in range(16):
        if response.status in {302, 303, 307}:
            target = urljoin(url, response.headers.get("location", ""))
            part = urlsplit(target)
            if f"{part.scheme}://{part.netloc}{part.path}" == CALLBACK:
                require(not part.fragment and not part.username and not part.password,
                        "Malformed Stage callback")
                query = parse_qs(part.query, keep_blank_values=True)
                require(set(query) == {"state", "code"} and query["state"] == [state] and
                        len(query["code"]) == 1 and bool(query["code"][0]),
                        "Stage callback state or code refused")
                return query["code"][0]
            require(stage_url(target) and target != url, "Stage redirect refused")
            url = target
            response = browser.request(url)
        elif response.status == 200:
            target, fields, headers = consent_request(url, response.body)
            response = browser.request(target, form=fields, headers=headers)
            url = target
        else:
            raise SmokeFailure("Stage authorization failed")
    raise SmokeFailure("Stage authorization exceeded redirect bound")


# Executed on stdin inside ONLY the existing Stage Axxium container. JWT and DB
# credentials never leave it; the returned short-lived JWT is captured privately.
# Session UUID is chosen before invocation so cleanup can recover uncertain output.
SESSION_SCRIPT = r"""
import pg from 'pg';
import {SignJWT} from 'jose';
import {createHash} from 'node:crypto';
const operation = process.env.COLAB_STAGE_SMOKE_OPERATION;
const id = process.env.COLAB_STAGE_SMOKE_SESSION;
const actor = 'actor_20603b93-2c96-4ff2-a213-0b1e84017993';
const origin = 'https://staging.axxium.promethean.rest';
let pool;
const deadline = setTimeout(() => process.exit(2), 12000);
deadline.unref();
try {
  if (!['create','delete'].includes(operation) || !/^[0-9a-f-]{36}$/.test(id || '') ||
      process.env.AXXIUM_PUBLIC_BASE_URL?.replace(/\/$/, '') !== origin ||
      !process.env.JWT_SECRET) throw Error('refused');
  pool = new pg.Pool({host:process.env.DB_HOST, port:Number(process.env.DB_PORT || 5432),
    database:process.env.DB_NAME, user:process.env.DB_USER, password:process.env.DB_PASSWORD,
    connectionTimeoutMillis:5000, query_timeout:5000, statement_timeout:5000});
  if (operation === 'delete') {
    const result = await pool.query('DELETE FROM sessions WHERE id=$1 AND actor_id=$2 RETURNING id', [id,actor]);
    const remaining = await pool.query('SELECT count(*)::int AS n FROM sessions WHERE id=$1', [id]);
    console.log(JSON.stringify({deleted:result.rowCount,remaining:remaining.rows[0].n}));
  } else {
    const result = await pool.query("SELECT a.id FROM actors a JOIN entities e ON e.id=a.entity_id WHERE a.id=$1 AND a.status='active' AND e.kind='human'", [actor]);
    if (result.rowCount !== 1) throw Error('refused');
    const token = await new SignJWT({sub:actor}).setJti(id).setProtectedHeader({alg:'HS256',typ:'JWT'})
      .setIssuedAt().setIssuer(process.env.JWT_ISSUER || 'axxium')
      .setAudience(process.env.JWT_AUDIENCE || 'promethean').setExpirationTime('5m')
      .sign(new TextEncoder().encode(process.env.JWT_SECRET));
    const digest = createHash('sha256').update(token).digest('hex');
    await pool.query("INSERT INTO sessions(id,actor_id,token_hash,expires_at) VALUES($1,$2,$3,clock_timestamp()+interval '5 minutes')", [id,actor,digest]);
    console.log(JSON.stringify({token,cookie:process.env.SESSION_COOKIE_NAME || 'axxium_session'}));
  }
} catch (_) { process.stderr.write('Stage synthetic session operation refused\n'); process.exitCode=1; }
finally { if(pool) await pool.end(); clearTimeout(deadline); }
"""


def stage_session(operation: str, session_id: str):
    require(operation in {"create", "delete"} and
            str(uuid.UUID(session_id)) == session_id, "Stage session operation refused")
    try:
        result = subprocess.run([
            "docker", "exec", "-i", "-w", "/app", "-e", "COLAB_STAGE_SMOKE_OPERATION=" + operation,
            "-e", "COLAB_STAGE_SMOKE_SESSION=" + session_id, CONTAINER,
            "node", "--input-type=module"], input=SESSION_SCRIPT,
            text=True, capture_output=True, timeout=20, check=False)
        require(result.returncode == 0 and not result.stderr and
                len(result.stdout) < 16384, "Stage session operation failed")
        return json.loads(result.stdout)
    except SmokeFailure:
        raise
    except Exception:
        raise SmokeFailure("Stage session operation failed") from None


def rpc(browser, bearer, method, params, number=1):
    response = browser.request(GATEWAY + "/mcp", data={
        "jsonrpc": "2.0", "id": number, "method": method, "params": params}, headers={
        "Authorization": "Bearer " + bearer,
        "Accept": "application/json, text/event-stream"})
    require(response.status == 200, "Authenticated Stage MCP request failed")
    data = response.json()
    require(data.get("jsonrpc") == "2.0" and data.get("id") == number and
            "error" not in data and isinstance(data.get("result"), dict),
            "Stage MCP response contract failed")
    return data["result"]


def tool(browser, bearer, name, arguments=None):
    return rpc(browser, bearer, "tools/call", {"name": name, "arguments": arguments or {}})


def initialized(browser, bearer):
    result = rpc(browser, bearer, "initialize", {"protocolVersion": "2025-03-26",
        "capabilities": {}, "clientInfo": {"name": "services-synthetic-stage-proof", "version": "1"}})
    require(result.get("protocolVersion") == "2025-03-26", "Stage MCP initialization failed")
    response = browser.request(GATEWAY + "/mcp", data={"jsonrpc": "2.0",
        "method": "notifications/initialized"}, headers={"Authorization": "Bearer " + bearer,
        "Accept": "application/json, text/event-stream"})
    require(response.status in {200, 202, 204}, "Stage MCP initialization notification failed")


def _smoke() -> dict:
    """Return only safe measured proof fields; raises after mandatory cleanup."""
    session_id = str(uuid.uuid4())
    browser = Browser()
    bearer = None
    complete = False
    cleaned = False
    try:
        session = stage_session("create", session_id)
        require(set(session) == {"token", "cookie"} and isinstance(session["token"], str),
                "Stage session response refused")
        browser.root_cookie(session["cookie"], session["token"])
        registration = browser.request(GATEWAY + "/register", data={
            "client_name": "Services synthetic Stage verification", "redirect_uris": [CALLBACK],
            "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "scope": "openid profile"})
        require(registration.status == 201, "Stage dynamic registration failed")
        client = registration.json().get("client_id")
        require(isinstance(client, str) and bool(client), "Stage registration response refused")
        verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        code = authorization_code(browser, GATEWAY + "/authorize?" + urlencode({
            "response_type": "code", "client_id": client, "redirect_uri": CALLBACK,
            "scope": "openid profile", "code_challenge": challenge, "code_challenge_method": "S256",
            "resource": GATEWAY + "/mcp", "state": state}), state)
        fields = {"grant_type": "authorization_code", "client_id": client, "code": code,
                  "redirect_uri": CALLBACK, "code_verifier": verifier, "resource": GATEWAY + "/mcp"}
        response = browser.request(GATEWAY + "/token", form=fields)
        require(response.status == 200, "Stage token exchange failed")
        bearer = response.json().get("access_token")
        require(isinstance(bearer, str) and bool(bearer), "Stage token response refused")
        require(browser.request(GATEWAY + "/token", form=fields).status == 400,
                "Stage authorization code replay was accepted")
        initialized(browser, bearer)
        names = {entry["name"] for entry in rpc(browser, bearer, "tools/list", {})["tools"]}
        require({"colab_status", "colab_tools", "colab_call", "colab_compute_usage",
                 "colab_compute_sessions", "colab_compute_start"} <= names, "Stage MCP tools missing")
        status = tool(browser, bearer, "colab_status").get("structuredContent", {})
        require(status.get("synthetic_upstreams") is True and
                status.get("bridge", {}).get("browser_connected") is False and
                status.get("bridge", {}).get("execution_verified") is False,
                "Stage bridge provenance failed")
        tools = tool(browser, bearer, "colab_tools").get("structuredContent", {}).get("result")
        require(isinstance(tools, list) and [x.get("name") for x in tools] == ["fixture_echo"],
                "Stage upstream tool discovery failed")
        echo = tool(browser, bearer, "colab_call", {"name": "fixture_echo",
                    "arguments": {"text": "services synthetic Stage assertion"}}).get("structuredContent", {})
        require(echo.get("fixture_echo") == "services synthetic Stage assertion" and
                echo.get("synthetic_upstreams") is True and echo.get("execution_verified") is False,
                "Stage real HTTP forwarding failed")
        usage = tool(browser, bearer, "colab_compute_usage").get("structuredContent", {})
        sessions = tool(browser, bearer, "colab_compute_sessions").get("structuredContent", {})
        require(usage.get("synthetic_upstreams") is True and "compute_units" in usage and
                usage["compute_units"] is None and usage.get("execution_verified") is False and
                sessions.get("synthetic_upstreams") is True and sessions.get("sessions") == [] and
                sessions.get("execution_verified") is False, "Stage compute provenance failed")
        require(tool(browser, bearer, "colab_compute_start", {
            "code": "raise RuntimeError('synthetic Stage must never execute')"}).get("isError") is True,
            "Stage compute execution was not refused")
        complete = True
    except SmokeFailure:
        raise
    except Exception:
        raise SmokeFailure("Stage synthetic OAuth proof failed") from None
    finally:
        cleanup = stage_session("delete", session_id)
        require(set(cleanup) == {"deleted", "remaining"} and
                type(cleanup["deleted"]) is int and cleanup["deleted"] in {0, 1} and
                type(cleanup["remaining"]) is int and cleanup["remaining"] == 0,
                "Stage synthetic session cleanup was not verified")
        cleaned = cleanup["deleted"] == 1
    require(complete and cleaned and bearer is not None, "Stage proof lacks its owned session cleanup")
    denied = browser.request(GATEWAY + "/mcp", data={"jsonrpc": "2.0", "id": 9,
        "method": "tools/list", "params": {}}, headers={"Authorization": "Bearer " + bearer,
        "Accept": "application/json, text/event-stream"})
    require(denied.status == 401, "Stage root-session revocation was not enforced")
    return {"schema": "colab-deployed-synthetic-oauth/v1", "passed": True,
            "synthetic_actor": True, "synthetic_upstreams": True,
            "human_login_verified": False, "google_execution_verified": False,
            "oauth_pkce": True, "code_replay_denied": True, "mcp_initialized": True,
            "forwarding_verified": True, "compute_reads_verified": True,
            "compute_execution_refused": True, "owned_root_session_removed": True,
            "root_session_revocation_verified": True,
            "oauth_flow_verified": True, "gateway_forwarding_verified": True,
            "execution_denied": True, "session_revoked": True}


def smoke() -> dict:
    """Count executed successful predicate assertions in this invocation only."""
    token = _assertions.set(0)
    try:
        result = _smoke()
        return {**result, "assertions": _assertions.get(),
                "assertion_count_kind": "executed successful Stage proof require predicates"}
    finally:
        _assertions.reset(token)


if __name__ == "__main__":
    try:
        print(json.dumps(smoke(), sort_keys=True))
    except Exception as error:
        print(json.dumps({"passed": False, "reason": str(error) if isinstance(error, SmokeFailure)
                          else "Stage proof failed"}))
        raise SystemExit(1)

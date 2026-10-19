"""Response controls outside ServerErrorMiddleware, including CORS preflight/500."""

import re

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class EndpointCORSMiddleware(CORSMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "OPTIONS"
            and b"access-control-request-method" in dict(scope.get("headers", []))
        ):
            path = scope["path"]
            allowed = {"GET"} if path in {"/health", "/api/v1/identity/me"} else set()
            if path == "/api/v1/vocabularies":
                allowed = {"GET"}
            elif re.fullmatch(
                r"/api/v1/vocabularies/[0-9a-fA-F-]{36}/concepts"
                r"(/[0-9a-fA-F-]{36})?",
                path,
            ):
                allowed = {"GET"}
            elif re.fullmatch(r"/api/v1/vocabularies/[0-9a-fA-F-]{36}/validate", path):
                allowed = {"POST"}
            if re.fullmatch(r"/api/v1/catalog/(works|instances|items|agents|subjects)", path):
                allowed = {"GET", "POST"}
            elif re.fullmatch(
                r"/api/v1/catalog/(works|instances|items|agents|subjects)/"
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                path,
            ):
                allowed = {"GET", "PATCH"}
            if re.fullmatch(
                r"/api/v1/catalog/(works|instances|items|agents|subjects)/validation", path
            ):
                allowed = {"POST"}
            elif path in {"/api/v1/catalog/profiles", "/api/v1/catalog/profiles/release"}:
                allowed = {"GET"}
            elif re.fullmatch(
                r"/api/v1/catalog/(works|instances|items|agents|subjects)/"
                r"[0-9a-fA-F-]{36}/relationships",
                path,
            ):
                allowed = {"GET", "PATCH"}
            elif re.fullmatch(
                r"/api/v1/catalog/(works|instances|items|agents|subjects)/"
                r"[0-9a-fA-F-]{36}/(editorial|public|history|audit|audit/[0-9a-fA-F-]{36}|"
                r"revisions|revisions/compare|revisions/[1-9][0-9]*(/snapshot)?)",
                path,
            ):
                allowed = {"GET"}
            elif re.fullmatch(
                r"/api/v1/catalog/(works|instances|items|agents|subjects)/"
                r"[0-9a-fA-F-]{36}/(editorial/(submit|approve|publish|return|withdraw|"
                r"republish|archive)|revisions/restore)",
                path,
            ):
                allowed = {"POST"}
            if re.fullmatch(
                r"/api/v1/catalog/public/(works|instances|items|agents|subjects)", path
            ):
                allowed = {"GET"}
            method = dict(scope["headers"])[b"access-control-request-method"].decode("latin-1")
            if not allowed:
                await PlainTextResponse("CORS endpoint rejected", status_code=400)(
                    scope, receive, send
                )
                return
            response = self.preflight_response(Headers(scope=scope))
            response.headers["Access-Control-Allow-Methods"] = ", ".join(sorted(allowed))
            if method not in allowed:
                response = PlainTextResponse(
                    "CORS method rejected",
                    status_code=400,
                    headers={
                        key: value
                        for key, value in response.headers.items()
                        if key.lower() != "content-length"
                    },
                )
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                policy = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
                    b"content-security-policy": (
                        b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
                    ),
                }
                # Interactive docs are development-only and use FastAPI's CDN/inline UI.
                if not self.production and scope["path"] in {"/docs", "/redoc"}:
                    policy[b"content-security-policy"] = (
                        b"default-src 'none'; script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
                        b"style-src https://cdn.jsdelivr.net 'unsafe-inline'; "
                        b"img-src https://fastapi.tiangolo.com data:; connect-src 'self'; "
                        b"frame-ancestors 'none'; base-uri 'none'"
                    )
                if self.production and scope["scheme"] == "https":
                    policy[b"strict-transport-security"] = b"max-age=31536000"
                supplied = scope.get("headers", [])
                response_headers = Headers(raw=list(message.get("headers", [])))
                vocabulary_cache = (
                    scope["method"] == "GET"
                    and re.fullmatch(
                        r"/api/v1/vocabularies(?:/[0-9a-fA-F-]{36}/concepts"
                        r"(?:/[0-9a-fA-F-]{36})?)?",
                        scope["path"],
                    )
                    is not None
                    and message["status"] in {200, 304}
                    and response_headers.get("cache-control")
                    == "private, no-cache, must-revalidate"
                )
                if (
                    scope["path"].startswith("/api/")
                    or any(key.lower() in {b"authorization", b"cookie"} for key, _ in supplied)
                    or message["status"] >= 400
                ) and not vocabulary_cache:
                    policy[b"cache-control"] = b"no-store, private"
                    policy[b"pragma"] = b"no-cache"
                elif vocabulary_cache:
                    policy.pop(b"cache-control", None)
                    policy.pop(b"pragma", None)
                headers = message.get("headers", [])
                message["headers"] = [
                    (key, value)
                    for key, value in headers
                    if key.lower() not in policy and key.lower() != b"server"
                ] + list(policy.items())
            await send(message)

        await self.app(scope, receive, protected_send)

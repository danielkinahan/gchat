"""Password authentication routes and middleware."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

COOKIE_NAME = "gchat_auth"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def register_auth_routes(app: FastAPI) -> None:
    password = os.environ.get("GCHAT_PASSWORD", "").strip()

    def auth_token() -> str:
        return hashlib.sha256(f"gchat:{password}".encode()).hexdigest()

    def is_authenticated(request: Request) -> bool:
        if not password:
            return True
        token = request.cookies.get(COOKIE_NAME, "")
        return hmac.compare_digest(token, auth_token())

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/api/auth/") or path in {"/api/restart", "/api/reload"}:
            return await call_next(request)
        if not is_authenticated(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        response = await call_next(request)
        if (
            request.method == "GET"
            and path.startswith("/api/")
            and path
            not in {
                "/api/runtime-state",
                "/api/link-preview",
                "/api/message-window",
                "/api/search",
            }
            and not path.startswith(("/api/auth/", "/api/media"))
        ):
            response.headers.setdefault("Cache-Control", "private, max-age=60")
        return response

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, bool]:
        return {
            "required": bool(password),
            "authenticated": is_authenticated(request),
        }

    @app.post("/api/auth/login")
    async def auth_login(request: Request, response: Response) -> dict[str, bool]:
        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass
        submitted = str(body.get("password", "")).strip()
        if password and not hmac.compare_digest(submitted, password):
            raise HTTPException(status_code=401, detail="Incorrect password")
        response.set_cookie(
            COOKIE_NAME,
            auth_token(),
            httponly=True,
            samesite="strict",
            max_age=COOKIE_MAX_AGE,
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def auth_logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, samesite="strict")
        return {"ok": True}

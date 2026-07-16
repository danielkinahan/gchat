"""External link preview route with SSRF protection and caching."""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx2 as httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
_SUCCESS_TTL_SECONDS = 60 * 60 * 24 * 7
_ERROR_TTL_SECONDS = 60 * 30
_TIMEOUT_SECONDS = 6.0
_MAX_BYTES = 1024 * 1024
_USER_AGENT = (
    "Mozilla/5.0 (compatible; gchat-link-preview/1.0; +https://github.com/gchat)"
)


def _is_safe_host(host: str) -> bool:
    if not host:
        return False
    lowered = host.strip().lower().rstrip(".")
    if lowered in {"localhost", "broadcasthost"} or lowered.endswith(".local"):
        return False
    try:
        addresses = socket.getaddrinfo(lowered, None)
    except OSError:
        return False
    for entry in addresses:
        try:
            address = ipaddress.ip_address(entry[4][0])
        except ValueError:
            continue
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def _select_meta(soup: BeautifulSoup, names: list[str]) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name})
        if tag is None:
            tag = soup.find("meta", attrs={"name": name})
        if tag is None:
            continue
        content = tag.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _fetch_preview(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must be http(s) with a host")
    if not _is_safe_host(parsed.hostname):
        raise ValueError("Host is not allowed")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(
        timeout=_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers=headers,
    ) as client:
        current_url = url
        for _ in range(6):
            current = urlparse(current_url)
            if (
                current.scheme not in ("http", "https")
                or not current.hostname
                or not _is_safe_host(current.hostname)
            ):
                raise ValueError("Redirect host is not allowed")
            with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response has no location")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type.lower():
                    raise ValueError(f"Unsupported content-type: {content_type}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_BYTES:
                        break
                raw = b"".join(chunks)[:_MAX_BYTES]
                final_url = str(response.url)
                encoding = response.encoding or "utf-8"
                break
        else:
            raise ValueError("Too many redirects")
    try:
        body = raw.decode(encoding, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(body, "html.parser")
    title = _select_meta(soup, ["og:title", "twitter:title"])
    if not title:
        title_tag = soup.find("title")
        if title_tag is not None and title_tag.string:
            title = title_tag.string.strip()
    description = _select_meta(
        soup,
        ["og:description", "twitter:description", "description"],
    )
    image = _select_meta(
        soup,
        ["og:image", "og:image:url", "twitter:image", "twitter:image:src"],
    )
    if image:
        image = urljoin(final_url, image)
    site_name = _select_meta(soup, ["og:site_name", "application-name"])
    if not site_name:
        site_name = urlparse(final_url).netloc
    favicon: str | None = None
    icon_link = soup.find(
        "link",
        rel=lambda value: bool(value) and "icon" in value.lower(),
    )
    if icon_link is not None:
        href = icon_link.get("href")
        if isinstance(href, str) and href.strip():
            favicon = urljoin(final_url, href.strip())
    return {
        "url": url,
        "resolved_url": final_url,
        "title": title,
        "description": description,
        "image": image,
        "site_name": site_name,
        "favicon": favicon,
    }


def register_preview_routes(app: FastAPI) -> None:
    @app.get("/api/link-preview")
    def link_preview(url: str) -> dict[str, Any]:
        cleaned = (url or "").strip()
        if not cleaned or len(cleaned) > 2048:
            raise HTTPException(status_code=400, detail="Invalid URL")
        now = time.time()
        with _CACHE_LOCK:
            cached = _CACHE.get(cleaned)
            if cached:
                ttl = (
                    _ERROR_TTL_SECONDS
                    if cached["data"].get("error")
                    else _SUCCESS_TTL_SECONDS
                )
                if (now - cached["fetched_at"]) < ttl:
                    return cached["data"]
        try:
            payload = _fetch_preview(cleaned)
        except (
            ValueError,
            httpx.HTTPError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            payload = {"url": cleaned, "error": str(exc)}
        except Exception as exc:
            payload = {"url": cleaned, "error": f"unexpected: {exc}"}
        with _CACHE_LOCK:
            if len(_CACHE) > 5000:
                _CACHE.clear()
            _CACHE[cleaned] = {"fetched_at": now, "data": payload}
        return payload

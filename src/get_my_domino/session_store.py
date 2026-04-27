"""Persistent HTTP session cookie storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, SupportsInt

import requests


class SessionStoreError(RuntimeError):
    """Raised when persisted session cookies cannot be read or written."""


def save_cookies(path: Path, cookies: requests.cookies.RequestsCookieJar) -> None:
    entries: list[dict[str, object]] = []
    for cookie in cookies:
        entries.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
        )

    payload = {"version": 1, "cookies": entries}
    _write_private(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def load_cookies(path: Path) -> requests.cookies.RequestsCookieJar:
    jar = requests.cookies.RequestsCookieJar()
    if not path.exists():
        return jar

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionStoreError(f"Unable to read session cookies from {path}: {exc}") from exc

    if not isinstance(cookies, list):
        raise SessionStoreError(f"Session cookie file {path} must contain a cookies list.")

    for raw_cookie in cookies:
        if not isinstance(raw_cookie, dict):
            continue
        name = _string_value(raw_cookie, "name")
        value = _string_value(raw_cookie, "value")
        domain = _string_value(raw_cookie, "domain")
        path_value = _string_value(raw_cookie, "path", default="/")
        if not name or value is None or not domain:
            continue
        jar.set(
            name,
            value,
            domain=domain,
            path=path_value or "/",
            secure=bool(raw_cookie.get("secure", False)),
            expires=_int_or_none(raw_cookie.get("expires")),
        )

    return jar


def clear_cookies(path: Path) -> None:
    path.unlink(missing_ok=True)


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def _string_value(data: dict[str, Any], key: str, *, default: str | None = None) -> str | None:
    value = data.get(key, default)
    if value is None:
        return None
    return str(value)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str | bytes | bytearray | SupportsInt):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None

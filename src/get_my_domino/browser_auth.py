"""Browser-assisted authentication for rivistadomino.it."""

from __future__ import annotations

import time

import requests

from .config import AppConfig
from .session_store import save_cookies


class BrowserAuthError(RuntimeError):
    """Raised when browser-assisted authentication fails."""


def login_with_browser(config: AppConfig) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserAuthError(
            "Browser login requires Playwright. Install it with "
            "`uv sync --extra browser` and run `uv run playwright install chromium`."
        ) from exc

    with sync_playwright() as playwright:
        browser = None
        last_error: Exception | None = None
        for channel in ("chrome", "msedge", None):
            try:
                if channel is None:
                    browser = playwright.chromium.launch(headless=False)
                else:
                    browser = playwright.chromium.launch(channel=channel, headless=False)
                break
            except Exception as exc:
                last_error = exc

        if browser is None:
            raise BrowserAuthError(
                f"Could not launch a browser ({last_error}). Install Google Chrome "
                "or run `uv run playwright install chromium`."
            )

        context = browser.new_context()
        page = context.new_page()
        page.goto(config.auth_login_url, wait_until="domcontentloaded")

        deadline = time.time() + config.auth_browser_timeout
        while time.time() < deadline:
            if not page.locator("form input[name='username'], form input[name='password']").count():
                break
            page.wait_for_timeout(1000)
        else:
            context.close()
            browser.close()
            raise BrowserAuthError(
                "Browser login timed out before an authenticated session appeared."
            )

        jar = requests.cookies.RequestsCookieJar()
        for cookie in context.cookies():
            domain = str(cookie.get("domain", ""))
            name = str(cookie.get("name", ""))
            value = str(cookie.get("value", ""))
            if not domain or not name:
                continue
            jar.set(
                name,
                value,
                domain=domain,
                path=str(cookie.get("path", "/")),
                secure=bool(cookie.get("secure", False)),
                expires=int(cookie["expires"]) if cookie.get("expires") else None,
            )

        context.close()
        browser.close()

    save_cookies(config.auth_session_path, jar)

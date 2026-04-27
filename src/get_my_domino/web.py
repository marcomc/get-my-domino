"""HTTP fetching and site discovery."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from .config import AppConfig
from .extract import article_date_from_url, extract_article, extract_links, issue_month_from_text
from .models import Article, Issue, Link
from .session_store import SessionStoreError, load_cookies, save_cookies


class FetchError(RuntimeError):
    """Raised when a page cannot be downloaded."""


class SessionLike(Protocol):
    headers: dict[str, str]
    cookies: requests.cookies.RequestsCookieJar

    def get(self, url: str, *, timeout: float) -> requests.Response: ...

    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: float,
        allow_redirects: bool,
    ) -> requests.Response: ...


@dataclass
class LoginForm:
    action_url: str
    payload: dict[str, str]


class WebClient:
    """Session-aware HTTP client for rivistadomino.it."""

    RETRYABLE_EXCEPTIONS = (requests.ConnectionError, requests.Timeout)

    def __init__(self, config: AppConfig, session: SessionLike | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent})
        self._authenticated = False

    def fetch_text(self, url: str) -> str:
        self.authenticate()
        return self._get_text(url)

    def authenticate(self) -> None:
        if self._authenticated:
            return

        if self._authenticate_with_saved_session():
            return

        if not self._has_credentials:
            return

        login_html = self._get_text(self.config.auth_login_url, authenticate=False)
        form = self._build_login_form(login_html)
        response = self._request(
            "POST",
            form.action_url,
            data=form.payload,
            allow_redirects=True,
        )
        if self._contains_login_form(response.text):
            raise FetchError("Authentication failed: login form is still present after submit.")
        self._authenticated = True
        self.save_session()

    def discover_issues(self) -> list[Link]:
        html = self.fetch_text(self.config.magazine_index_url)
        return extract_links(
            html,
            page_url=self.config.magazine_index_url,
            include_patterns=self.config.issue_link_patterns,
            skip_patterns=self.config.skip_link_patterns,
        )

    def discover_articles(self, issue_url: str) -> list[Link]:
        return self.discover_issue(issue_url).articles

    def discover_issue(self, issue_url: str) -> Issue:
        html = self.fetch_text(issue_url)
        return self._extract_issue(html, page_url=issue_url)

    def discover_feed_articles(self, *, max_pages: int = 1) -> list[Link]:
        links: list[Link] = []
        seen: set[str] = set()
        page_url: str | None = self.config.feed_index_url
        pages_read = 0

        while page_url and pages_read < max_pages:
            html = self._get_text(page_url, authenticate=False)
            page_links = extract_links(
                html,
                page_url=page_url,
                include_patterns=self.config.feed_article_link_patterns,
                skip_patterns=self.config.skip_link_patterns,
            )
            for link in page_links:
                if link.url in seen:
                    continue
                seen.add(link.url)
                links.append(link)
            page_url = self._next_page_url(html, page_url=page_url)
            pages_read += 1

        return links

    def discover_weekly_articles(self, *, max_pages: int = 1) -> list[Link]:
        return self.discover_feed_articles(max_pages=max_pages)

    def download_article(self, article_url: str) -> Article:
        html = self.fetch_text(article_url)
        return extract_article(
            html,
            page_url=article_url,
            content_selectors=self.config.content_selectors,
        )

    @property
    def _has_credentials(self) -> bool:
        return bool(self.config.auth_username and self.config.auth_password)

    def save_session(self) -> None:
        try:
            save_cookies(self.config.auth_session_path, self.session.cookies)
        except SessionStoreError as exc:
            raise FetchError(str(exc)) from exc

    def clear_session(self) -> None:
        self.session.cookies.clear()
        self._authenticated = False

    def _authenticate_with_saved_session(self) -> bool:
        if not self.config.auth_session_path.exists():
            return False
        try:
            self.session.cookies.update(load_cookies(self.config.auth_session_path))
        except SessionStoreError as exc:
            raise FetchError(str(exc)) from exc

        response = self._request("GET", self.config.auth_login_url)
        if self._contains_login_form(response.text):
            self.session.cookies.clear()
            return False
        self._authenticated = True
        self.save_session()
        return True

    def _get_text(self, url: str, *, authenticate: bool = True) -> str:
        if authenticate:
            self.authenticate()
        response = self._request("GET", url)
        if authenticate and self._contains_login_form(response.text):
            if self._has_credentials:
                self._authenticated = False
                self.session.cookies.clear()
                self.authenticate()
                response = self._request("GET", url)
            if self._contains_login_form(response.text):
                raise FetchError(
                    "Authentication required. Run `get-my-domino login --browser` "
                    "or configure auth_username/auth_password."
                )
        return response.text

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        last_retryable_error: requests.RequestException | None = None
        for attempt in range(3):
            try:
                if method == "POST":
                    response = self.session.post(
                        url,
                        data=data or {},
                        timeout=self.config.request_timeout,
                        allow_redirects=allow_redirects,
                    )
                else:
                    response = self.session.get(url, timeout=self.config.request_timeout)
                response.raise_for_status()
                return response
            except self.RETRYABLE_EXCEPTIONS as exc:
                last_retryable_error = exc
                if attempt < 2:
                    print(
                        f"progress: retry {attempt + 2}/3 {method} {url} after {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break
            except requests.RequestException as exc:
                raise FetchError(f"Unable to fetch {url}: {exc}") from exc
        raise FetchError(f"Unable to fetch {url}: {last_retryable_error}") from last_retryable_error

    def _build_login_form(self, html: str) -> LoginForm:
        soup = BeautifulSoup(html, "html.parser")
        form = self._find_login_form(soup)
        payload = self._form_payload(form)
        payload[self.config.auth_username_field] = self.config.auth_username
        payload[self.config.auth_password_field] = self.config.auth_password
        payload[self.config.auth_submit_field] = self.config.auth_submit_value

        action = str(form.get("action") or self.config.auth_login_url)
        return LoginForm(action_url=urljoin(self.config.auth_login_url, action), payload=payload)

    def _find_login_form(self, soup: BeautifulSoup) -> Tag:
        for form in soup.find_all("form"):
            if not isinstance(form, Tag):
                continue
            if form.find(attrs={"name": self.config.auth_username_field}) and form.find(
                attrs={"name": self.config.auth_password_field}
            ):
                return form
        raise FetchError(
            "Authentication failed: login form does not contain the configured username "
            "and password fields."
        )

    def _form_payload(self, form: Tag) -> dict[str, str]:
        payload: dict[str, str] = {}
        for element in form.find_all(["input", "button"]):
            if not isinstance(element, Tag):
                continue
            name = element.get("name")
            if not name:
                continue
            input_type = str(element.get("type", "")).lower()
            if input_type in {"checkbox", "radio"} and not element.has_attr("checked"):
                continue
            payload[str(name)] = str(element.get("value", ""))
        return payload

    def _contains_login_form(self, html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        try:
            self._find_login_form(soup)
        except FetchError:
            return False
        return True

    def _next_page_url(self, html: str, *, page_url: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.find("link", rel="next")
        if isinstance(next_link, Tag) and next_link.get("href"):
            return urljoin(page_url, str(next_link.get("href")))
        anchor = soup.find("a", class_="next")
        if isinstance(anchor, Tag) and anchor.get("href"):
            return urljoin(page_url, str(anchor.get("href")))
        return None

    def _extract_issue(self, html: str, *, page_url: str) -> Issue:
        soup = BeautifulSoup(html, "html.parser")
        title_element = soup.select_one("h1.product_title, h1.entry-title, h1")
        title = (
            " ".join(title_element.get_text(" ", strip=True).split())
            if title_element is not None
            else page_url
        )
        summary = soup.select_one(".summary, .entry-summary")
        summary_text = (
            summary.get_text(" ", strip=True)
            if summary is not None
            else soup.get_text(" ", strip=True)
        )
        published_month = issue_month_from_text(summary_text)

        article_panel = soup.select_one("#tab-articles")
        if article_panel is None:
            return Issue(
                title=title,
                url=page_url,
                published_month=published_month,
                articles=extract_links(
                    html,
                    page_url=page_url,
                    include_patterns=self.config.article_link_patterns,
                    skip_patterns=self.config.skip_link_patterns,
                ),
            )

        articles: list[Link] = []
        current_group: str | None = None
        order = 1
        for element in article_panel.find_all(["h3", "a"]):
            if not isinstance(element, Tag):
                continue
            text = " ".join(element.get_text(" ", strip=True).split())
            if not text:
                continue
            if element.name == "h3":
                current_group = text
                continue
            href = element.get("href")
            classes = element.get("class")
            if not href or not isinstance(classes, list) or "article_title" not in classes:
                continue
            absolute_url = urljoin(page_url, str(href))
            articles.append(
                Link(
                    title=text,
                    url=absolute_url.rstrip("/"),
                    group=current_group,
                    published_date=article_date_from_url(absolute_url),
                    order=order,
                )
            )
            order += 1

        return Issue(title=title, url=page_url, published_month=published_month, articles=articles)


def fetch_text(url: str, config: AppConfig) -> str:
    return WebClient(config).fetch_text(url)


def discover_issues(config: AppConfig) -> list[Link]:
    return WebClient(config).discover_issues()


def discover_articles(issue_url: str, config: AppConfig) -> list[Link]:
    return WebClient(config).discover_articles(issue_url)


def discover_issue(issue_url: str, config: AppConfig) -> Issue:
    return WebClient(config).discover_issue(issue_url)


def download_article(article_url: str, config: AppConfig) -> Article:
    return WebClient(config).download_article(article_url)


def discover_feed_articles(config: AppConfig, *, max_pages: int = 1) -> list[Link]:
    return WebClient(config).discover_feed_articles(max_pages=max_pages)


def discover_weekly_articles(config: AppConfig, *, max_pages: int = 1) -> list[Link]:
    return discover_feed_articles(config, max_pages=max_pages)

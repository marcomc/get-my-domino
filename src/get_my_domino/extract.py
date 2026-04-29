"""HTML link and article extraction."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from .models import Article, Link

REMOVABLE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    ".advertisement",
    ".ads",
    ".ad",
    ".cookie",
    ".cookies",
    ".share",
    ".social",
    ".related",
    ".entry-header",
    ".post-thumbnail",
    ".wp-post-image",
    "img[src*='domini-trasparente']",
)


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", parsed.query, "")
    )


def slugify(value: str, *, fallback: str = "item") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or fallback


def article_date_from_url(url: str) -> str | None:
    match = re.search(r"/blog/(\d{4})/(\d{2})/(\d{2})/", url)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def issue_month_from_text(value: str) -> str | None:
    match = re.search(r"\b(\d{1,2})/(\d{4})\b", value)
    if not match:
        return None
    month = int(match.group(1))
    if month < 1 or month > 12:
        return None
    return f"{match.group(2)}-{month:02d}"


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    lowered = value.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def extract_links(
    html: str,
    *,
    page_url: str,
    include_patterns: Iterable[str],
    skip_patterns: Iterable[str],
) -> list[Link]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[Link] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor.get("href", "")).strip()
        title = _clean_text(anchor.get_text(" ", strip=True))
        absolute_url = normalize_url(urljoin(page_url, href))
        haystack = f"{href} {title} {absolute_url}"
        if not title or _matches_any(haystack, skip_patterns):
            continue
        if include_patterns and not _matches_any(haystack, include_patterns):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(
            Link(title=title, url=absolute_url, published_date=article_date_from_url(absolute_url))
        )

    return links


def extract_article(
    html: str,
    *,
    page_url: str,
    content_selectors: Iterable[str],
) -> Article:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else page_url

    for selector in REMOVABLE_SELECTORS:
        for element in soup.select(selector):
            element.decompose()
    _remove_empty_wrappers(soup)

    content: Tag | BeautifulSoup = soup
    for selector in content_selectors:
        selected = soup.select_one(selector)
        if selected is not None:
            content = selected
            break

    heading = content.find(["h1", "h2"]) if isinstance(content, Tag) else soup.find(["h1", "h2"])
    if heading is not None:
        heading_text = _clean_text(heading.get_text(" ", strip=True))
        if heading_text:
            title = heading_text
    author = _extract_author(soup, content=content, heading=heading)

    clean_html = str(content)
    text = "\n\n".join(
        block
        for block in (_clean_text(part) for part in content.get_text("\n", strip=True).split("\n"))
        if block
    )
    return Article(
        title=title,
        url=normalize_url(page_url),
        html=clean_html,
        text=text,
        author=author,
    )


def _extract_author(
    soup: BeautifulSoup,
    *,
    content: Tag | BeautifulSoup,
    heading: Tag | None,
) -> str | None:
    meta_author = _extract_author_from_meta(soup)
    if meta_author:
        return meta_author

    selectors = (
        "h1 + .article_byline",
        "h1 + div.article_byline a",
        ".article_byline",
        "[rel='author']",
        ".author",
        ".byline",
        ".entry-author",
        ".post-author",
    )
    for selector in selectors:
        element = soup.select_one(selector)
        if element is None:
            continue
        author = _clean_explicit_author(element.get_text(" ", strip=True))
        if author:
            return author

    if heading is not None:
        sibling = heading.find_next_sibling()
        hops = 0
        while isinstance(sibling, Tag) and hops < 4:
            author = _clean_author(sibling.get_text(" ", strip=True))
            if author:
                return author
            sibling = sibling.find_next_sibling()
            hops += 1

    for index, element in enumerate(content.find_all(["p", "div", "h3", "h4", "span", "a"])):
        if not isinstance(element, Tag):
            continue
        if index >= 8:
            break
        author = _clean_author(element.get_text(" ", strip=True))
        if author:
            return author
    return None


def _extract_author_from_meta(soup: BeautifulSoup) -> str | None:
    selectors = (
        "meta[name='author']",
        "meta[property='author']",
        "meta[name='twitter:data1']",
    )
    for selector in selectors:
        element = soup.select_one(selector)
        if not isinstance(element, Tag):
            continue
        author = _clean_explicit_author(str(element.get("content", "")))
        if author:
            return author
    return None


def _clean_explicit_author(value: str) -> str | None:
    cleaned = _strip_author_prefix(value)
    if not cleaned or len(cleaned) > 80:
        return None
    if not re.search(r"\s", cleaned):
        return None
    if re.search(r"[!?:;,]", cleaned):
        return None
    if re.search(r"\d", cleaned):
        return None
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ.'’`\- ]+", cleaned):
        return None
    return cleaned.strip()


def _clean_author(value: str) -> str | None:
    cleaned = _strip_author_prefix(value)
    if not cleaned or len(cleaned) > 80:
        return None
    if not re.search(r"\s", cleaned):
        return None
    if re.search(r"[.!?:;]", cleaned):
        return None
    return cleaned


def _strip_author_prefix(value: str) -> str:
    cleaned = _clean_text(value)
    match = re.fullmatch(
        r"(?:di|by|da|diretta\s+da|diretto\s+da)\s+(.+)", cleaned, flags=re.IGNORECASE
    )
    if match:
        cleaned = match.group(1).strip()
    return cleaned


def _remove_empty_wrappers(soup: BeautifulSoup) -> None:
    for element in soup.find_all(["a", "p", "figure", "div"]):
        if not isinstance(element, Tag):
            continue
        if element.get_text(strip=True):
            continue
        if element.find(["img", "video", "audio", "iframe", "source"]):
            continue
        element.decompose()

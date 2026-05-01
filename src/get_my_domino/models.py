"""Domain models for rivistadomino.it exports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Link:
    title: str
    url: str
    group: str | None = None
    author: str | None = None
    published_date: str | None = None
    order: int | None = None
    summary: str | None = None


@dataclass(frozen=True)
class Issue:
    title: str
    url: str
    issue_code: str | None
    articles: list[Link]
    cover_image_url: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    html: str
    text: str
    issue_title: str | None = None
    author: str | None = None

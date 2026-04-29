"""Configurable audiobook filename rendering."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass

from .models import Issue

DEFAULT_AUDIOBOOK_MAGAZINE_TITLE = "Domino"
DEFAULT_AUDIOBOOK_FILENAME_SEPARATOR = "-"
DEFAULT_AUDIOBOOK_FILENAME_FORMAT = "{magazine_slug}{sep}{year}{sep}{number}{sep}{title_slug}"

_SAFE_SEPARATOR_RE = re.compile(r"[-_.]+")
_ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


@dataclass(frozen=True)
class AudiobookFilenameSettings:
    magazine_title: str = DEFAULT_AUDIOBOOK_MAGAZINE_TITLE
    separator: str = DEFAULT_AUDIOBOOK_FILENAME_SEPARATOR
    format_template: str = DEFAULT_AUDIOBOOK_FILENAME_FORMAT


def validate_audiobook_separator(value: str) -> str:
    if not value:
        raise ValueError("Audiobook filename separator cannot be empty.")
    if not _SAFE_SEPARATOR_RE.fullmatch(value):
        raise ValueError(
            "Audiobook filename separator must use only safe characters such as '-', '_' or '.'."
        )
    return value


def validate_audiobook_format(format_template: str) -> str:
    allowed_fields = {
        "magazine",
        "magazine_slug",
        "sep",
        "year",
        "number",
        "issue",
        "issue_compact",
        "title",
        "title_slug",
    }
    formatter = string.Formatter()
    for _, field_name, _, _ in formatter.parse(format_template):
        if field_name is None:
            continue
        if field_name not in allowed_fields:
            raise ValueError(
                "Unknown audiobook filename field "
                f"{field_name!r}. Allowed fields: {', '.join(sorted(allowed_fields))}."
            )
    return format_template


def render_audiobook_filename(
    *,
    issue_title: str,
    year: str,
    number: str,
    settings: AudiobookFilenameSettings,
) -> str:
    separator = validate_audiobook_separator(settings.separator)
    format_template = validate_audiobook_format(settings.format_template)
    issue_title_clean = _clean_filename_text(issue_title)
    magazine_clean = _clean_filename_text(settings.magazine_title)
    values = {
        "magazine": magazine_clean,
        "magazine_slug": _slug_text(magazine_clean, separator=separator),
        "sep": separator,
        "year": year,
        "number": number,
        "issue": f"{year}{separator}{number}",
        "issue_compact": f"{year}{number}",
        "title": issue_title_clean,
        "title_slug": _slug_text(issue_title_clean, separator=separator),
    }
    rendered = format_template.format_map(values)
    cleaned = _clean_filename_text(rendered)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise ValueError("Audiobook filename format rendered an empty filename.")
    return cleaned


def render_audiobook_filename_for_issue(
    issue: Issue,
    *,
    settings: AudiobookFilenameSettings,
) -> str:
    year, number = issue_year_number(issue)
    return render_audiobook_filename(
        issue_title=issue.title,
        year=year,
        number=number,
        settings=settings,
    )


def render_audiobook_filename_from_tags(
    *,
    title: str,
    date: str,
    issue_code: str | None,
    settings: AudiobookFilenameSettings,
) -> str:
    year, number = year_number_from_tags(date=date, issue_code=issue_code)
    return render_audiobook_filename(
        issue_title=title,
        year=year,
        number=number,
        settings=settings,
    )


def issue_year_number(issue: Issue) -> tuple[str, str]:
    issue_code = issue.issue_code
    if issue_code and re.fullmatch(r"\d{4}-\d{2}", issue_code):
        return tuple(issue_code.split("-", 1))  # type: ignore[return-value]
    raise ValueError(f"Issue {issue.title!r} does not expose a valid issue code.")


def year_number_from_tags(*, date: str, issue_code: str | None = None) -> tuple[str, str]:
    if issue_code and re.fullmatch(r"\d{4}-\d{2}", issue_code):
        return tuple(issue_code.split("-", 1))  # type: ignore[return-value]
    match = re.match(r"(\d{4})-(\d{2})", date)
    if not match:
        raise ValueError(f"Audiobook date tag {date!r} does not contain a YYYY-NN prefix.")
    return match.group(1), match.group(2)


def _clean_filename_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", _ILLEGAL_FILENAME_CHARS_RE.sub(" ", value)).strip()
    return cleaned


def _slug_text(value: str, *, separator: str) -> str:
    cleaned = _clean_filename_text(value).lower()
    cleaned = re.sub(r"[^0-9a-zà-öø-ÿ]+", separator, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(re.escape(separator) + r"{2,}", separator, cleaned)
    return cleaned.strip(separator) or "item"

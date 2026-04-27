"""Local export storage for downloaded articles."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .extract import slugify
from .models import Article


def article_dir(output_dir: Path, article: Article, *, index: int) -> Path:
    return output_dir / f"{index:03d}-{slugify(article.title)}"


def write_article(output_dir: Path, article: Article, *, index: int) -> Path:
    target_dir = article_dir(output_dir, article, index=index)
    write_article_export(target_dir, article)
    return target_dir


def write_article_named(parent_dir: Path, article: Article, *, name: str) -> Path:
    target_dir = parent_dir / name
    write_article_export(target_dir, article)
    return target_dir


def write_article_export(target_dir: Path, article: Article) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "article.html").write_text(article.html, encoding="utf-8")
    article_text = article_text_document(article)
    (target_dir / "article.txt").write_text(article_text, encoding="utf-8")
    (target_dir / "article.rtf").write_text(_rtf_document(article_text), encoding="ascii")
    (target_dir / "metadata.json").write_text(
        json.dumps(asdict(article), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def article_text_document(article: Article) -> str:
    heading = [
        line
        for line in (
            _clean_heading(article.issue_title),
            _clean_heading(article.title),
            _author_line(article.author),
        )
        if line
    ]
    body = article.text.strip()
    return "\n".join([*heading, "", body]).rstrip() + "\n"


def missing_article_export_files(target_dir: Path) -> list[str]:
    expected = ("article.html", "article.txt", "article.rtf", "metadata.json")
    return [name for name in expected if not (target_dir / name).exists()]


def _rtf_document(text: str) -> str:
    return "{\\rtf1\\ansi\\ansicpg65001\\uc1\n" + _rtf_escape(text) + "\n}\n"


def _rtf_escape(text: str) -> str:
    parts: list[str] = []
    for char in text:
        if char == "\n":
            parts.append("\\par\n")
        elif char in {"\\", "{", "}"}:
            parts.append("\\" + char)
        elif 0x20 <= ord(char) <= 0x7E:
            parts.append(char)
        else:
            parts.extend(_rtf_unicode_escape(char))
    return "".join(parts)


def _rtf_unicode_escape(char: str) -> list[str]:
    escapes: list[str] = []
    for index in range(0, len(char.encode("utf-16-be")), 2):
        code_unit = int.from_bytes(char.encode("utf-16-be")[index : index + 2], "big")
        if code_unit >= 0x8000:
            code_unit -= 0x10000
        escapes.append(f"\\u{code_unit}?")
    return escapes


def _clean_heading(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    for suffix in (" - Rivista Domino", " - Domino"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned or None


def _author_line(value: str | None) -> str | None:
    cleaned = _clean_heading(value)
    if not cleaned:
        return None
    if cleaned.lower().startswith("di "):
        return cleaned
    return f"di {cleaned}"


def manifest_path(output_dir: Path) -> Path:
    return output_dir / "manifest.json"


def read_manifest(output_dir: Path) -> dict[str, str]:
    path = manifest_path(output_dir)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest {path} must contain a JSON object.")
    return {str(key): str(value) for key, value in data.items()}


def write_manifest(output_dir: Path, manifest: dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path(output_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

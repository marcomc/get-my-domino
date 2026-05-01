"""Local export storage for downloaded articles."""

from __future__ import annotations

import filecmp
import json
from datetime import UTC, datetime
from pathlib import Path

from .extract import slugify
from .models import Article

_NORMALIZED_ARTIFACT_EXTENSIONS = ("html", "txt", "rtf", "m4a")


def article_dir(output_dir: Path, article: Article, *, index: int) -> Path:
    return output_dir / f"{index:03d}-{slugify(article.title)}"


def article_basename(target_dir: Path) -> str:
    return target_dir.name


def article_text_path(target_dir: Path) -> Path:
    normalize_article_artifacts(target_dir)
    return target_dir / f"{article_basename(target_dir)}.txt"


def write_article(
    output_dir: Path,
    article: Article,
    *,
    index: int,
    export_formats: tuple[str, ...],
    metadata: dict[str, object] | None = None,
) -> Path:
    target_dir = article_dir(output_dir, article, index=index)
    write_article_export(target_dir, article, export_formats=export_formats, metadata=metadata)
    return target_dir


def write_article_named(
    parent_dir: Path,
    article: Article,
    *,
    name: str,
    export_formats: tuple[str, ...],
    metadata: dict[str, object] | None = None,
) -> Path:
    target_dir = parent_dir / name
    write_article_export(target_dir, article, export_formats=export_formats, metadata=metadata)
    return target_dir


def write_article_export(
    target_dir: Path,
    article: Article,
    *,
    export_formats: tuple[str, ...],
    metadata: dict[str, object] | None = None,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    normalize_article_artifacts(target_dir)
    _remove_legacy_export_files(target_dir)
    basename = article_basename(target_dir)
    article_text = article_text_document(article)
    if "html" in export_formats:
        (target_dir / f"{basename}.html").write_text(article.html, encoding="utf-8")
    if "txt" in export_formats:
        (target_dir / f"{basename}.txt").write_text(article_text, encoding="utf-8")
    if "rtf" in export_formats:
        (target_dir / f"{basename}.rtf").write_text(_rtf_document(article_text), encoding="ascii")
    _remove_unselected_export_files(target_dir, export_formats)
    (target_dir / "metadata.json").write_text(
        json.dumps(
            article_metadata(article, metadata=metadata),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def write_article_metadata(
    target_dir: Path,
    article: Article,
    *,
    metadata: dict[str, object] | None = None,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    normalize_article_artifacts(target_dir)
    (target_dir / "metadata.json").write_text(
        json.dumps(
            article_metadata(article, metadata=metadata),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _remove_legacy_export_files(target_dir: Path) -> None:
    for name in ("article.html", "article.txt", "article.rtf"):
        (target_dir / name).unlink(missing_ok=True)


def _remove_unselected_export_files(target_dir: Path, export_formats: tuple[str, ...]) -> None:
    basename = article_basename(target_dir)
    for extension in ("html", "txt", "rtf"):
        if extension not in export_formats:
            (target_dir / f"{basename}.{extension}").unlink(missing_ok=True)


def article_metadata(
    article: Article, *, metadata: dict[str, object] | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": _clean_heading(article.title) or article.title,
        "url": article.url,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if article.issue_title:
        payload["issue_title"] = article.issue_title
    if article.author:
        payload["author"] = article.author
    if metadata:
        payload.update({key: value for key, value in metadata.items() if value is not None})
    return payload


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


def missing_article_export_files(target_dir: Path, *, export_formats: tuple[str, ...]) -> list[str]:
    normalize_article_artifacts(target_dir)
    basename = article_basename(target_dir)
    expected = [f"{basename}.{extension}" for extension in export_formats]
    expected.append("metadata.json")
    return [name for name in expected if not (target_dir / name).exists()]


def normalize_article_artifacts(target_dir: Path) -> None:
    basename = article_basename(target_dir)
    for extension in _NORMALIZED_ARTIFACT_EXTENSIONS:
        _normalize_article_artifact(target_dir, basename=basename, extension=extension)


def _normalize_article_artifact(target_dir: Path, *, basename: str, extension: str) -> None:
    canonical_path = target_dir / f"{basename}.{extension}"
    legacy_paths = _legacy_artifact_paths(
        target_dir,
        canonical_path=canonical_path,
        extension=extension,
    )
    if not canonical_path.exists() and len(legacy_paths) == 1:
        legacy_paths[0].rename(canonical_path)
        legacy_paths = []
    if not canonical_path.exists():
        return
    for legacy_path in legacy_paths:
        if legacy_path.name == f"article.{extension}" or filecmp.cmp(
            canonical_path,
            legacy_path,
            shallow=False,
        ):
            legacy_path.unlink(missing_ok=True)


def _legacy_artifact_paths(
    target_dir: Path,
    *,
    canonical_path: Path,
    extension: str,
) -> list[Path]:
    paths: list[Path] = []
    for candidate in sorted(target_dir.glob(f"*.{extension}")):
        if candidate == canonical_path:
            continue
        if candidate.name.count(".") != 1:
            continue
        paths.append(candidate)
    return paths


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


def issue_metadata_path(issue_dir: Path) -> Path:
    return issue_dir / "issue.json"


def write_issue_metadata(issue_dir: Path, payload: dict[str, object]) -> Path:
    issue_dir.mkdir(parents=True, exist_ok=True)
    path = issue_metadata_path(issue_dir)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (issue_dir / "metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path

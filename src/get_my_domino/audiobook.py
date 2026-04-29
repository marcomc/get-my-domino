"""Issue-level audiobook packaging helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class AudiobookError(RuntimeError):
    """Raised when issue audiobook packaging fails."""


@dataclass(frozen=True)
class AudiobookChapter:
    title: str
    audio_path: Path
    contributor: str | None = None


def build_m4b(
    output_path: Path,
    *,
    title: str,
    chapters: list[AudiobookChapter],
    cover_image_path: Path | None = None,
    metadata: dict[str, str] | None = None,
) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None:
        raise AudiobookError("Command 'ffmpeg' is required for issue audiobook packaging.")
    if ffprobe is None:
        raise AudiobookError("Command 'ffprobe' is required for issue audiobook packaging.")
    if not chapters:
        raise AudiobookError("Issue audiobook packaging requires at least one chapter.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    for chapter in chapters:
        if chapter.audio_path.suffix.lower() != ".m4a":
            raise AudiobookError(
                "Issue audiobook packaging currently requires m4a chapter audio files."
            )
        if not chapter.audio_path.exists():
            raise AudiobookError(f"Missing chapter audio file: {chapter.audio_path}")

    with tempfile.TemporaryDirectory(prefix="get-my-domino-m4b-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        concat_path = temp_dir / "chapters.txt"
        metadata_path = temp_dir / "chapters.ffmeta"
        concat_path.write_text(_concat_manifest(chapters), encoding="utf-8")
        metadata_path.write_text(
            _ffmetadata(title, chapters, ffprobe=ffprobe, metadata=metadata),
            encoding="utf-8",
        )

        command = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-f",
            "ffmetadata",
            "-i",
            str(metadata_path),
        ]
        if cover_image_path is not None:
            if not cover_image_path.exists():
                raise AudiobookError(f"Cover image not found: {cover_image_path}")
            command.extend(
                [
                    "-i",
                    str(cover_image_path),
                ]
            )
        command.extend(
            [
                "-map",
                "0:a:0",
                "-map_metadata",
                "1",
                "-map_chapters",
                "1",
                "-c:a",
                "copy",
            ]
        )
        if cover_image_path is not None:
            cover_codec = _cover_codec(cover_image_path)
            command.extend(
                [
                    "-map",
                    "2:v:0",
                    "-c:v",
                    cover_codec,
                    "-disposition:v:0",
                    "attached_pic",
                ]
            )
        command.append(str(output_path))

        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise AudiobookError(
                f"ffmpeg failed while packaging {output_path.name}: {detail}"
            ) from exc
    return output_path


def _concat_manifest(chapters: list[AudiobookChapter]) -> str:
    return "".join(f"file {_ffmpeg_quote(chapter.audio_path)}\n" for chapter in chapters)


def _ffmetadata(
    title: str,
    chapters: list[AudiobookChapter],
    *,
    ffprobe: str,
    metadata: dict[str, str] | None,
) -> str:
    header = {"title": title, "album": title, "artist": "Rivista Domino"}
    if metadata:
        header.update(metadata)
    lines = [
        ";FFMETADATA1",
        *[f"{key}={_ffmetadata_escape(value)}" for key, value in header.items()],
    ]
    start_ms = 0
    for chapter in chapters:
        duration_ms = _chapter_duration_ms(chapter.audio_path, ffprobe=ffprobe)
        end_ms = start_ms + duration_ms
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={_ffmetadata_escape(chapter.title)}",
            ]
        )
        if chapter.contributor:
            lines.append(f"artist={_ffmetadata_escape(chapter.contributor)}")
        start_ms = end_ms
    return "\n".join(lines) + "\n"


def _chapter_duration_ms(audio_path: Path, *, ffprobe: str) -> int:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise AudiobookError(f"ffprobe failed for {audio_path.name}: {detail}") from exc
    try:
        seconds = float(result.stdout.strip())
    except ValueError as exc:
        raise AudiobookError(
            f"ffprobe returned an invalid duration for {audio_path.name}."
        ) from exc
    return max(1, int(seconds * 1000))


def _ffmpeg_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _ffmetadata_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\n", " ")
    )


def _cover_codec(cover_image_path: Path) -> str:
    suffix = cover_image_path.suffix.lower()
    if suffix == ".png":
        return "png"
    if suffix in {".jpg", ".jpeg"}:
        return "copy"
    return "png"


def read_audiobook_tags(path: Path) -> dict[str, str]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise AudiobookError("Command 'ffprobe' is required for audiobook metadata inspection.")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format_tags",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise AudiobookError(f"ffprobe failed for {path.name}: {detail}") from exc
    payload = json.loads(result.stdout)
    format_payload = payload.get("format")
    if not isinstance(format_payload, dict):
        return {}
    tags = format_payload.get("tags")
    if not isinstance(tags, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in tags.items()
        if isinstance(key, str) and isinstance(value, str)
    }

from __future__ import annotations

import subprocess
from pathlib import Path

from pytest import MonkeyPatch

from get_my_domino.audiobook import AudiobookChapter, build_m4b


def test_build_m4b_places_cover_input_before_cover_mapping(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    audio_path = tmp_path / "chapter.m4a"
    cover_path = tmp_path / "cover.png"
    output_path = tmp_path / "book.m4b"
    audio_path.write_bytes(b"audio")
    cover_path.write_bytes(b"cover")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str:
        return f"/usr/bin/{name}"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "ffprobe" in command[0]:
            return subprocess.CompletedProcess(command, 0, stdout="12.5\n", stderr="")
        if "ffmpeg" in command[0]:
            output_path.write_bytes(b"book")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)

    build_m4b(
        output_path,
        title="Example Issue",
        chapters=[AudiobookChapter(title="Chapter 1", audio_path=audio_path)],
        cover_image_path=cover_path,
    )

    ffmpeg_command = next(command for command in commands if "ffmpeg" in command[0])
    cover_input_index = ffmpeg_command.index(str(cover_path))
    first_map_index = ffmpeg_command.index("-map")
    cover_map_index = ffmpeg_command.index("2:v:0")
    assert ffmpeg_command[cover_input_index - 1] == "-i"
    assert cover_input_index < first_map_index
    assert first_map_index < cover_map_index


def test_build_m4b_writes_chapter_contributor_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    audio_path = tmp_path / "chapter.m4a"
    output_path = tmp_path / "book.m4b"
    audio_path.write_bytes(b"audio")
    captured_metadata: dict[str, str] = {}

    def fake_which(name: str) -> str:
        return f"/usr/bin/{name}"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "ffprobe" in command[0]:
            return subprocess.CompletedProcess(command, 0, stdout="12.5\n", stderr="")
        if "ffmpeg" in command[0]:
            metadata_path = Path(command[command.index("ffmetadata") + 2])
            captured_metadata["text"] = metadata_path.read_text(encoding="utf-8")
            output_path.write_bytes(b"book")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)

    build_m4b(
        output_path,
        title="Example Issue",
        chapters=[
            AudiobookChapter(
                title="Chapter 1 (di Dario Fabbri)",
                audio_path=audio_path,
                contributor="Dario Fabbri",
            )
        ],
        metadata={"composer": "Dario Fabbri", "contributors": "Dario Fabbri"},
    )

    ffmetadata = captured_metadata["text"]
    assert "composer=Dario Fabbri" in ffmetadata
    assert "contributors=Dario Fabbri" in ffmetadata
    assert "title=Chapter 1 (di Dario Fabbri)" in ffmetadata
    assert "artist=Dario Fabbri" in ffmetadata

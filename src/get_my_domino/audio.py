"""macOS Siri-compatible speech synthesis helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_AUDIO_FORMATS = ("m4a", "mp3")


class AudioError(RuntimeError):
    """Raised when local speech synthesis fails."""


@dataclass(frozen=True)
class SayVoice:
    name: str
    locale: str
    sample: str


def normalize_audio_format(value: str) -> str:
    normalized = value.strip().lower().removeprefix(".")
    if normalized == "mp4a":
        normalized = "m4a"
    if normalized not in SUPPORTED_AUDIO_FORMATS:
        supported = ", ".join(SUPPORTED_AUDIO_FORMATS)
        raise ValueError(f"audio_format must be one of: {supported}.")
    return normalized


def available_say_voices(*, locale_prefix: str | None = None) -> list[SayVoice]:
    say = shutil.which("say")
    if say is None:
        raise AudioError("macOS command 'say' is required.")

    result = subprocess.run(
        [say, "-v", "?"],
        check=True,
        capture_output=True,
        text=True,
    )
    voices: list[SayVoice] = []
    for line in result.stdout.splitlines():
        match = re.match(
            r"(?P<name>.+?)\s+(?P<locale>[a-z]{2}_[A-Z0-9]{2,3})\s+#\s*(?P<sample>.*)", line
        )
        if not match:
            continue
        voice = SayVoice(
            name=match.group("name").strip(),
            locale=match.group("locale"),
            sample=match.group("sample").strip(),
        )
        if locale_prefix is None or voice.locale.startswith(locale_prefix):
            voices.append(voice)
    return voices


def validate_say_voice(voice: str | None) -> None:
    if not voice:
        return
    voices = available_say_voices()
    if voice in {candidate.name for candidate in voices}:
        return
    italian = ", ".join(candidate.name for candidate in voices if candidate.locale == "it_IT")
    hint = f" Available Italian voices: {italian}." if italian else ""
    raise AudioError(
        f"Voice '{voice}' is not available to macOS say and would fall back silently."
        f"{hint} Run `get-my-domino voices` to list supported voice names."
    )


def synthesize_audio(
    text_path: Path,
    output_path: Path,
    *,
    voice: str | None,
    audio_format: str,
) -> Path:
    say = shutil.which("say")
    if say is None:
        raise AudioError("macOS command 'say' is required.")

    normalized_format = normalize_audio_format(audio_format)
    validate_say_voice(voice)
    aiff_path = output_path.with_suffix(".aiff")
    command = [say, "-f", str(text_path), "-o", str(aiff_path)]
    if voice:
        command.extend(["-v", voice])
    try:
        _run_interruptible(command)
        convert_args = _conversion_args(normalized_format, aiff_path, output_path)
        _run_interruptible(convert_args)
    finally:
        aiff_path.unlink(missing_ok=True)
    return output_path


def _run_interruptible(command: list[str]) -> None:
    process = subprocess.Popen(command)
    try:
        return_code = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)


def synthesize_m4a(text_path: Path, output_path: Path, *, voice: str | None) -> Path:
    return synthesize_audio(text_path, output_path, voice=voice, audio_format="m4a")


def _conversion_args(audio_format: str, aiff_path: Path, output_path: Path) -> list[str]:
    if audio_format == "m4a":
        afconvert = shutil.which("afconvert")
        if afconvert is None:
            raise AudioError("macOS command 'afconvert' is required for m4a audio.")
        return [afconvert, "-f", "m4af", "-d", "aac", str(aiff_path), str(output_path)]
    if audio_format == "mp3":
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise AudioError("Command 'ffmpeg' is required for mp3 audio.")
        return [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(aiff_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_path),
        ]
    raise ValueError(f"Unsupported audio format: {audio_format}")

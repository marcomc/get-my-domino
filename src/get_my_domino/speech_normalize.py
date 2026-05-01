"""AI-assisted speech text normalization."""

from __future__ import annotations

import difflib
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

SPEECH_PROMPT_VERSION = "2026-04-28.1"
SUPPORTED_SPEECH_AGENTS = ("codex", "codex-cloud", "github-cli", "github-copilot", "jelly")
CODEX_NORMALIZER_MAX_ATTEMPTS = 3


class SpeechNormalizeError(RuntimeError):
    """Raised when speech text normalization fails."""


@dataclass(frozen=True)
class SpeechNormalizeSettings:
    enabled: bool
    agent: str
    command: str
    model: str
    timeout: float
    force: bool
    fallback: bool
    prompt_path: Path | None = None
    diff: bool = False


@dataclass(frozen=True)
class SpeechNormalizeResult:
    path: Path
    changed: bool
    diff_text: str


def speech_text_path(source_text_path: Path) -> Path:
    return source_text_path.with_suffix(".speech.txt")


def speech_log_path(source_text_path: Path) -> Path:
    return source_text_path.with_suffix(".speech.log")


def ensure_speech_text(
    source_text_path: Path,
    settings: SpeechNormalizeSettings,
) -> Path:
    return normalize_speech_text(source_text_path, settings).path


def normalize_speech_text(
    source_text_path: Path,
    settings: SpeechNormalizeSettings,
) -> SpeechNormalizeResult:
    if not settings.enabled:
        return SpeechNormalizeResult(path=source_text_path, changed=False, diff_text="")
    if not source_text_path.exists():
        raise SpeechNormalizeError(f"Text file not found: {source_text_path}")

    output_path = speech_text_path(source_text_path)
    if _can_reuse(source_text_path, output_path, force=settings.force):
        return SpeechNormalizeResult(path=output_path, changed=False, diff_text="")

    try:
        normalized_text = _mechanical_prepass(source_text_path.read_text(encoding="utf-8"))
        if settings.agent == "codex":
            _run_codex_normalizer(source_text_path, output_path, normalized_text, settings)
        elif settings.agent in SUPPORTED_SPEECH_AGENTS:
            raise SpeechNormalizeError(
                f"Speech normalizer agent '{settings.agent}' is not implemented yet."
            )
        else:
            supported = ", ".join(SUPPORTED_SPEECH_AGENTS)
            raise SpeechNormalizeError(
                f"Unknown speech normalizer agent '{settings.agent}'. Supported: {supported}."
            )
    except Exception as exc:
        if settings.fallback:
            return SpeechNormalizeResult(path=source_text_path, changed=False, diff_text="")
        if isinstance(exc, SpeechNormalizeError):
            raise
        raise SpeechNormalizeError(str(exc)) from exc

    if not output_path.exists():
        raise SpeechNormalizeError(f"Speech normalizer did not create: {output_path}")
    _finalize_output(output_path)
    diff_text = _speech_diff(source_text_path, output_path) if settings.diff else ""
    return SpeechNormalizeResult(path=output_path, changed=True, diff_text=diff_text)


def _can_reuse(source_text_path: Path, output_path: Path, *, force: bool) -> bool:
    if force or not output_path.exists():
        return False
    return output_path.stat().st_mtime >= source_text_path.stat().st_mtime


def _run_codex_normalizer(
    source_text_path: Path,
    output_path: Path,
    normalized_text: str,
    settings: SpeechNormalizeSettings,
) -> None:
    max_attempts = 1 if settings.fallback else CODEX_NORMALIZER_MAX_ATTEMPTS
    command_path = shutil.which(settings.command) or settings.command
    log_path = speech_log_path(source_text_path)
    prompt = _codex_prompt(
        source_text_path=source_text_path,
        output_path=output_path,
        normalized_text=normalized_text,
        prompt_path=settings.prompt_path,
    )
    command = [
        command_path,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(source_text_path.parent),
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(source_text_path.with_suffix(".speech.last-message.txt")),
    ]
    if settings.model:
        command.extend(["-m", settings.model])
    command.append("-")

    attempts: list[str] = []
    last_error: SpeechNormalizeError | None = None
    for attempt in range(1, max_attempts + 1):
        if output_path.exists():
            output_path.unlink()
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=settings.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append(
                _codex_attempt_log(
                    command=command,
                    source_text_hash=_sha256_text(normalized_text),
                    settings=settings,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    status=f"timeout after {settings.timeout:g} seconds",
                    returncode=None,
                    stdout=exc.stdout,
                    stderr=exc.stderr,
                )
            )
            last_error = SpeechNormalizeError(
                f"codex normalizer timed out after {settings.timeout:g} seconds"
            )
            continue
        attempts.append(
            _codex_attempt_log(
                command=command,
                source_text_hash=_sha256_text(normalized_text),
                settings=settings,
                attempt=attempt,
                max_attempts=max_attempts,
                status="completed",
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
        if result.returncode == 0:
            log_path.write_text("\n\n".join(attempts), encoding="utf-8")
            return
        last_error = SpeechNormalizeError(
            f"codex normalizer failed with exit code {result.returncode}"
        )
    log_path.write_text("\n\n".join(attempts), encoding="utf-8")
    if last_error is not None:
        raise last_error
    raise SpeechNormalizeError("codex normalizer failed without returning a result")


def _codex_attempt_log(
    *,
    command: list[str],
    source_text_hash: str,
    settings: SpeechNormalizeSettings,
    attempt: int,
    max_attempts: int,
    status: str,
    returncode: int | None,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
) -> str:
    stdout_text = _decode_subprocess_output(stdout)
    stderr_text = _decode_subprocess_output(stderr)
    return "\n".join(
        [
            f"timestamp: {datetime.now(UTC).isoformat(timespec='seconds')}",
            f"agent: {settings.agent}",
            f"attempt: {attempt}/{max_attempts}",
            f"status: {status}",
            f"command: {' '.join(command)}",
            f"prompt_path: {settings.prompt_path or 'packaged default'}",
            f"source_sha256: {source_text_hash}",
            f"returncode: {returncode if returncode is not None else 'timeout'}",
            "",
            "stdout:",
            stdout_text,
            "",
            "stderr:",
            stderr_text,
        ]
    )


def _decode_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _codex_prompt(
    *,
    source_text_path: Path,
    output_path: Path,
    normalized_text: str,
    prompt_path: Path | None,
) -> str:
    template = _read_prompt_template(prompt_path)
    return template.format(
        source_text_path=source_text_path,
        output_path=output_path,
        normalized_text=normalized_text,
    )


def _read_prompt_template(prompt_path: Path | None) -> str:
    if prompt_path is not None and prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        files("get_my_domino.prompts")
        .joinpath("speech-normalize-codex.txt")
        .read_text(encoding="utf-8")
    )


def _mechanical_prepass(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?m)^https?://\S+\s*$", "", text)
    text = re.sub(r"\n+\s*([.,;:!?])", r"\1", text)
    return text.strip() + "\n"


def _finalize_output(output_path: Path) -> None:
    text = output_path.read_text(encoding="utf-8")
    output_path.write_text(text.strip() + "\n", encoding="utf-8")


def _speech_diff(source_text_path: Path, output_path: Path) -> str:
    source_lines = source_text_path.read_text(encoding="utf-8").splitlines(keepends=True)
    output_lines = output_path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            source_lines,
            output_lines,
            fromfile=str(source_text_path),
            tofile=str(output_path),
        )
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

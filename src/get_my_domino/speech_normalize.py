"""AI-assisted speech text normalization."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from .storage import read_article_metadata, update_article_metadata

SPEECH_PROMPT_VERSION = "2026-04-28.1"
SUPPORTED_SPEECH_AGENTS = ("codex", "codex-cloud", "github-cli", "github-copilot", "jelly")
CODEX_NORMALIZER_MAX_ATTEMPTS = 3
PACKAGED_PROMPT_PATH = "package:get_my_domino/prompts/speech-normalize-codex.txt"


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


@dataclass(frozen=True)
class PromptTemplateInfo:
    path: str
    version: str
    sha256: str
    template_text: str


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
    normalized_text = _mechanical_prepass(source_text_path.read_text(encoding="utf-8"))
    prompt_info = _prompt_template_info(settings.prompt_path)
    source_text_sha256 = _sha256_text(normalized_text)
    if _can_reuse(
        source_text_path,
        output_path,
        force=settings.force,
        settings=settings,
        prompt_info=prompt_info,
        source_text_sha256=source_text_sha256,
    ):
        return SpeechNormalizeResult(path=output_path, changed=False, diff_text="")

    try:
        if settings.agent == "codex":
            _run_codex_normalizer(
                source_text_path,
                output_path,
                normalized_text,
                settings,
                prompt_info=prompt_info,
                source_text_sha256=source_text_sha256,
            )
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
    _write_speech_metadata(
        source_text_path,
        output_path,
        settings=settings,
        prompt_info=prompt_info,
        source_text_sha256=source_text_sha256,
    )
    diff_text = _speech_diff(source_text_path, output_path) if settings.diff else ""
    return SpeechNormalizeResult(path=output_path, changed=True, diff_text=diff_text)


def _can_reuse(
    source_text_path: Path,
    output_path: Path,
    *,
    force: bool,
    settings: SpeechNormalizeSettings,
    prompt_info: PromptTemplateInfo,
    source_text_sha256: str,
) -> bool:
    if force or not output_path.exists():
        return False
    if output_path.stat().st_mtime < source_text_path.stat().st_mtime:
        return False
    metadata = _speech_metadata(source_text_path)
    if metadata is None:
        return False
    return (
        metadata.get("normalizer_agent") == settings.agent
        and metadata.get("normalizer_command") == settings.command
        and metadata.get("normalizer_model") == settings.model
        and metadata.get("prompt_path") == prompt_info.path
        and metadata.get("prompt_version") == prompt_info.version
        and metadata.get("prompt_sha256") == prompt_info.sha256
        and metadata.get("source_text_sha256") == source_text_sha256
    )


def _run_codex_normalizer(
    source_text_path: Path,
    output_path: Path,
    normalized_text: str,
    settings: SpeechNormalizeSettings,
    *,
    prompt_info: PromptTemplateInfo,
    source_text_sha256: str,
) -> None:
    max_attempts = 1 if settings.fallback else CODEX_NORMALIZER_MAX_ATTEMPTS
    command_path = shutil.which(settings.command) or settings.command
    log_path = speech_log_path(source_text_path)
    prompt = _codex_prompt(
        source_text_path=source_text_path,
        output_path=output_path,
        normalized_text=normalized_text,
        prompt_info=prompt_info,
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
                    source_text_hash=source_text_sha256,
                    settings=settings,
                    prompt_info=prompt_info,
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
                source_text_hash=source_text_sha256,
                settings=settings,
                prompt_info=prompt_info,
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
    prompt_info: PromptTemplateInfo,
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
            f"prompt_path: {prompt_info.path}",
            f"prompt_version: {prompt_info.version}",
            f"prompt_sha256: {prompt_info.sha256}",
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
    prompt_info: PromptTemplateInfo,
) -> str:
    return prompt_info.template_text.format(
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


def _prompt_template_info(prompt_path: Path | None) -> PromptTemplateInfo:
    template_text = _read_prompt_template(prompt_path)
    resolved_prompt_path = (
        str(prompt_path)
        if prompt_path is not None and prompt_path.exists()
        else PACKAGED_PROMPT_PATH
    )
    return PromptTemplateInfo(
        path=resolved_prompt_path,
        version=SPEECH_PROMPT_VERSION,
        sha256=_sha256_text(template_text),
        template_text=template_text,
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


def _speech_metadata(source_text_path: Path) -> dict[str, object] | None:
    try:
        metadata = read_article_metadata(source_text_path.parent)
    except (ValueError, json.JSONDecodeError):
        return None
    speech_metadata = metadata.get("speech_normalization")
    if not isinstance(speech_metadata, dict):
        return None
    return {str(key): value for key, value in speech_metadata.items()}


def _write_speech_metadata(
    source_text_path: Path,
    output_path: Path,
    *,
    settings: SpeechNormalizeSettings,
    prompt_info: PromptTemplateInfo,
    source_text_sha256: str,
) -> None:
    metadata_path = source_text_path.parent / "metadata.json"
    if not metadata_path.exists():
        return
    update_article_metadata(
        source_text_path.parent,
        {
            "speech_normalization": {
                "normalizer_agent": settings.agent,
                "normalizer_command": settings.command,
                "normalizer_model": settings.model,
                "prompt_path": prompt_info.path,
                "prompt_sha256": prompt_info.sha256,
                "prompt_version": prompt_info.version,
                "source_text_sha256": source_text_sha256,
                "normalized_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "normalized_output_path": str(output_path),
            }
        },
    )

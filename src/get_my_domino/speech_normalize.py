"""AI-assisted speech text normalization."""

from __future__ import annotations

import difflib
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SPEECH_PROMPT_VERSION = "2026-04-28.1"
SUPPORTED_SPEECH_AGENTS = ("codex", "codex-cloud", "github-cli", "github-copilot", "jelly")


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
    command_path = shutil.which(settings.command) or settings.command
    log_path = speech_log_path(source_text_path)
    prompt = _codex_prompt(
        source_text_path=source_text_path,
        output_path=output_path,
        normalized_text=normalized_text,
    )
    command = [
        command_path,
        "exec",
        "--skip-git-repo-check",
        "--cd",
        str(source_text_path.parent),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "--output-last-message",
        str(log_path.with_suffix(".speech.last-message.txt")),
    ]
    if settings.model:
        command.extend(["-m", settings.model])
    command.append("-")

    result = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=settings.timeout,
        check=False,
    )
    log_path.write_text(
        "\n".join(
            [
                f"timestamp: {datetime.now(UTC).isoformat(timespec='seconds')}",
                f"agent: {settings.agent}",
                f"command: {' '.join(command)}",
                f"source_sha256: {_sha256_text(normalized_text)}",
                f"returncode: {result.returncode}",
                "",
                "stdout:",
                result.stdout,
                "",
                "stderr:",
                result.stderr,
            ]
        ),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise SpeechNormalizeError(f"codex normalizer failed with exit code {result.returncode}")


def _codex_prompt(*, source_text_path: Path, output_path: Path, normalized_text: str) -> str:
    return f"""You are preparing an Italian geopolitics article for macOS text-to-speech.
Read the input text below and write only the corrected speech-ready text to this file:

{output_path}

Do not print the article text in your final answer. Do not emit markdown, XML, SSML,
comments, explanations, or notes. Preserve meaning, facts, sequence, authorial style,
and wording. Do not summarize, translate, simplify, or add content.

Only make minimal orthographic, punctuation, spacing, line-break, and
pronunciation-oriented changes needed for natural Italian TTS.

Rules:
- Fix typographic wordplay that harms pronunciation when the intended spoken word is clear:
  "(ri)tornava" -> "ritornava"; "transita(va)" -> "transitava" when the context is past tense;
  "Donald(o)" -> "Donaldo" when it is a deliberate spoken pun.
- Repair extraction line breaks. Rejoin isolated one-word or short foreign terms when they
  syntactically belong to the surrounding sentence, for example "prossima al\\n\\nJahannam\\n\\n."
  should become "prossima al Jahannam."
- Convert dash inserts that sound unnatural only when equivalent, for example
  "– si fa per dire –" -> "(si fa per dire),".
- Restore Italian stress marks only when context strongly disambiguates pronunciation:
  "i princìpi della geopolitica" versus "i prìncipi sauditi"; "subìto" only when it means
  suffered; "ancóra" only when the intended stress requires it. Leave uncertain cases unchanged.
- Preserve foreign names, transliterations, original scripts, and geopolitical terms.
- For English or loan expressions likely to be misread by Italian TTS, apply only conservative
  plain-text pronunciation aids that remain readable and do not alter meaning.

Input source path for traceability: {source_text_path}

Input text:
<<<GET_MY_DOMINO_SPEECH_INPUT
{normalized_text}
GET_MY_DOMINO_SPEECH_INPUT
"""


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

"""macOS Siri-compatible speech synthesis helpers."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import (
    CancelledError as FutureCancelledError,
)
from concurrent.futures import (
    Future,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event

SUPPORTED_AUDIO_FORMATS = ("m4a", "mp3")
AudioProgressCallback = Callable[[str, Path | None, int | None], None]


class AudioError(RuntimeError):
    """Raised when local speech synthesis fails."""


class _AudioInterrupted(RuntimeError):
    """Internal cancellation used to stop sibling chunk workers cleanly."""


@dataclass(frozen=True)
class SayVoice:
    name: str
    locale: str
    sample: str


@dataclass(frozen=True)
class _AiffChunk:
    form_type: bytes
    extra_chunks: tuple[tuple[bytes, bytes], ...]
    comm: bytes
    channels: int
    sample_size: int
    sound_data: bytes


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
    timeout: float | None = None,
    progress: AudioProgressCallback | None = None,
    chunked: bool = True,
    chunk_chars: int = 2500,
    concurrency: int = 3,
    retries: int = 2,
    stall_timeout: float | None = 45.0,
) -> Path:
    say = shutil.which("say")
    if say is None:
        raise AudioError("macOS command 'say' is required.")

    normalized_format = normalize_audio_format(audio_format)
    validate_say_voice(voice)
    aiff_path = output_path.with_suffix(".aiff")
    try:
        with _audio_lock(progress=progress):
            if chunked:
                _synthesize_chunked_aiff(
                    say,
                    text_path,
                    aiff_path,
                    voice=voice,
                    timeout=timeout,
                    progress=progress,
                    chunk_chars=chunk_chars,
                    concurrency=concurrency,
                    retries=retries,
                    stall_timeout=stall_timeout,
                )
            else:
                command = _say_command(say, text_path, aiff_path, voice=voice)
                progress and progress("synthesizing", aiff_path, 0)
                _run_interruptible(
                    command,
                    timeout=timeout,
                    stall_timeout=stall_timeout,
                    monitor_path=aiff_path,
                    progress=progress,
                )
        convert_args = _conversion_args(normalized_format, aiff_path, output_path)
        progress and progress("converting", output_path, None)
        _run_interruptible(convert_args, timeout=timeout)
    finally:
        aiff_path.unlink(missing_ok=True)
    return output_path


def _run_interruptible(
    command: list[str],
    *,
    timeout: float | None = None,
    stall_timeout: float | None = None,
    monitor_path: Path | None = None,
    progress: AudioProgressCallback | None = None,
    stop_event: Event | None = None,
) -> None:
    process = subprocess.Popen(command)
    started_at = time.monotonic()
    last_size: int | None = None
    last_growth_at = started_at
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise _AudioInterrupted
            try:
                return_code = process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if timeout is not None and time.monotonic() - started_at >= timeout:
                    raise
                if monitor_path is not None and progress is not None and monitor_path.exists():
                    size = monitor_path.stat().st_size
                    if size != last_size:
                        last_size = size
                        last_growth_at = time.monotonic()
                        progress("aiff_growth", monitor_path, size)
                if (
                    stall_timeout is not None
                    and monitor_path is not None
                    and time.monotonic() - last_growth_at >= stall_timeout
                ):
                    raise subprocess.TimeoutExpired(command, stall_timeout) from None
    except _AudioInterrupted:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    except KeyboardInterrupt:
        if stop_event is not None:
            stop_event.set()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        command_name = Path(command[0]).name
        timeout_label = f"{timeout:g}" if timeout is not None else "unknown"
        raise AudioError(
            f"Audio command timed out after {timeout_label} seconds: {command_name}"
        ) from exc
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)


@contextmanager
def _audio_lock(*, progress: AudioProgressCallback | None = None) -> Iterator[None]:
    lock_path = Path(
        os.environ.get(
            "GET_MY_DOMINO_AUDIO_LOCK_PATH",
            str(Path.home() / ".cache" / "get-my-domino" / "audio.lock"),
        )
    ).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            progress and progress("waiting_lock", lock_path, None)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        progress and progress("lock_acquired", lock_path, None)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


def _say_command(say: str, text_path: Path, aiff_path: Path, *, voice: str | None) -> list[str]:
    command = [say, "-f", str(text_path), "-o", str(aiff_path)]
    if voice:
        command.extend(["-v", voice])
    return command


def _synthesize_chunked_aiff(
    say: str,
    text_path: Path,
    combined_aiff_path: Path,
    *,
    voice: str | None,
    timeout: float | None,
    progress: AudioProgressCallback | None,
    chunk_chars: int,
    concurrency: int,
    retries: int,
    stall_timeout: float | None,
) -> None:
    chunks = _split_text_chunks(text_path.read_text(encoding="utf-8"), chunk_chars=chunk_chars)
    if len(chunks) == 1:
        command = _say_command(say, text_path, combined_aiff_path, voice=voice)
        progress and progress("synthesizing", combined_aiff_path, 0)
        _run_interruptible(
            command,
            timeout=timeout,
            stall_timeout=stall_timeout,
            monitor_path=combined_aiff_path,
            progress=progress,
        )
        return

    with tempfile.TemporaryDirectory(prefix="get-my-domino-audio-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        chunk_paths: list[Path] = []
        aiff_paths: list[Path] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_path = temp_dir / f"chunk-{index:03d}.txt"
            aiff_path = temp_dir / f"chunk-{index:03d}.aiff"
            chunk_path.write_text(chunk, encoding="utf-8")
            chunk_paths.append(chunk_path)
            aiff_paths.append(aiff_path)

        max_workers = min(max(1, concurrency), len(chunk_paths))
        progress and progress("chunking", combined_aiff_path, len(chunk_paths))
        stop_event = Event()
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {
            executor.submit(
                _synthesize_one_chunk,
                say,
                chunk_path,
                aiff_path,
                voice=voice,
                timeout=timeout,
                progress=progress,
                retries=retries,
                stall_timeout=stall_timeout,
                stop_event=stop_event,
            ): aiff_path
            for chunk_path, aiff_path in zip(chunk_paths, aiff_paths, strict=True)
        }
        try:
            for future in as_completed(futures):
                future.result()
        except KeyboardInterrupt:
            stop_event.set()
            _cancel_chunk_futures(futures)
            _wait_for_chunk_futures(futures)
            raise
        except Exception:
            stop_event.set()
            _cancel_chunk_futures(futures)
            _wait_for_chunk_futures(futures)
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        _merge_aiff_files(aiff_paths, combined_aiff_path)


def _synthesize_one_chunk(
    say: str,
    chunk_path: Path,
    aiff_path: Path,
    *,
    voice: str | None,
    timeout: float | None,
    progress: AudioProgressCallback | None,
    retries: int,
    stall_timeout: float | None,
    stop_event: Event | None,
) -> None:
    command = _say_command(say, chunk_path, aiff_path, voice=voice)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if stop_event is not None and stop_event.is_set():
            raise _AudioInterrupted
        aiff_path.unlink(missing_ok=True)
        try:
            progress and progress("synthesizing", aiff_path, 0)
            _run_interruptible(
                command,
                timeout=timeout,
                stall_timeout=stall_timeout,
                monitor_path=aiff_path,
                progress=progress,
                stop_event=stop_event,
            )
            return
        except _AudioInterrupted:
            raise
        except (AudioError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt >= retries:
                break
            progress and progress("retrying", aiff_path, attempt + 1)
    chunk_label = chunk_path.stem.removeprefix("chunk-")
    message = f"say chunk {chunk_label} failed after {retries + 1} attempt(s)"
    raise AudioError(message) from last_error


def _cancel_chunk_futures(futures: dict[Future[None], Path]) -> None:
    for future in futures:
        future.cancel()


def _wait_for_chunk_futures(futures: dict[Future[None], Path]) -> None:
    for future in futures:
        if future.cancelled():
            continue
        try:
            future.result(timeout=5)
        except (
            KeyboardInterrupt,
            _AudioInterrupted,
            AudioError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FutureCancelledError,
            FutureTimeoutError,
        ):
            continue


def _split_text_chunks(text: str, *, chunk_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]
    if not paragraphs:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        paragraph_parts = _split_long_paragraph(paragraph, chunk_chars=chunk_chars)
        for part in paragraph_parts:
            part_len = len(part) + (2 if current else 0)
            if current and current_len + part_len > chunk_chars:
                chunks.append("\n\n".join(current).strip() + "\n")
                current = [part]
                current_len = len(part)
            else:
                current.append(part)
                current_len += part_len
    if current:
        chunks.append("\n\n".join(current).strip() + "\n")
    return chunks


def _split_long_paragraph(paragraph: str, *, chunk_chars: int) -> list[str]:
    if len(paragraph) <= chunk_chars:
        return [paragraph]
    words = paragraph.split()
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        word_len = len(word) + (1 if current else 0)
        if current and current_len + word_len > chunk_chars:
            parts.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += word_len
    if current:
        parts.append(" ".join(current))
    return parts


def _merge_aiff_files(aiff_paths: list[Path], output_path: Path) -> None:
    parsed_chunks = [_read_aiff_chunk(path) for path in aiff_paths]
    if not parsed_chunks:
        raise AudioError("No AIFF chunks were generated.")

    first = parsed_chunks[0]
    bytes_per_frame = first.channels * (first.sample_size // 8)
    if bytes_per_frame <= 0:
        raise AudioError("Unsupported AIFF sample size.")

    total_sound_data = b"".join(chunk.sound_data for chunk in parsed_chunks)
    total_frames = len(total_sound_data) // bytes_per_frame
    comm = first.comm[:2] + total_frames.to_bytes(4, "big") + first.comm[6:]
    ssnd_payload = (0).to_bytes(4, "big") + (0).to_bytes(4, "big") + total_sound_data
    extra_chunks = b"".join(
        _aiff_chunk_bytes(chunk_id, payload) for chunk_id, payload in first.extra_chunks
    )
    comm_chunk = _aiff_chunk_bytes(b"COMM", comm)
    ssnd_chunk = _aiff_chunk_bytes(b"SSND", ssnd_payload)
    form_payload = first.form_type + extra_chunks + comm_chunk + ssnd_chunk
    output_path.write_bytes(b"FORM" + len(form_payload).to_bytes(4, "big") + form_payload)


def _read_aiff_chunk(path: Path) -> _AiffChunk:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in {b"AIFF", b"AIFC"}:
        raise AudioError(f"Unsupported AIFF chunk: {path}")
    form_type = data[8:12]
    offset = 12
    comm: bytes | None = None
    sound_data: bytes | None = None
    extra_chunks: list[tuple[bytes, bytes]] = []
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        payload = data[payload_start:payload_end]
        if chunk_id == b"COMM":
            comm = payload
        elif chunk_id == b"SSND":
            if len(payload) < 8:
                raise AudioError(f"Malformed AIFF SSND chunk: {path}")
            sound_offset = int.from_bytes(payload[:4], "big")
            sound_data = payload[8 + sound_offset :]
        elif chunk_id == b"FVER":
            extra_chunks.append((chunk_id, payload))
        offset = payload_end + (chunk_size % 2)
    if comm is None or len(comm) < 18 or sound_data is None:
        raise AudioError(f"Missing AIFF audio data: {path}")
    channels, _, sample_size = struct.unpack(">hLh", comm[:8])
    if channels <= 0:
        raise AudioError(f"Unsupported AIFF channel count: {path}")
    return _AiffChunk(
        form_type=form_type,
        extra_chunks=tuple(extra_chunks),
        comm=comm,
        channels=channels,
        sample_size=sample_size,
        sound_data=sound_data,
    )


def _aiff_chunk_bytes(chunk_id: bytes, payload: bytes) -> bytes:
    padding = b"\x00" if len(payload) % 2 else b""
    return chunk_id + len(payload).to_bytes(4, "big") + payload + padding

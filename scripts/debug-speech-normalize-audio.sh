#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  cat >&2 <<'USAGE'
usage: scripts/debug-speech-normalize-audio.sh ARTICLE_TXT [AUDIO_PATH]

Regenerates ARTICLE_TXT.speech.txt with Codex, backs up the existing audio,
regenerates audio from the speech text, and prints approximate timestamps for
small text changes that may affect TTS prosody.
USAGE
  exit 2
fi

article_txt=$1
audio_path=${2:-}

if [[ ! -f "${article_txt}" ]]; then
  echo "error: article text not found: ${article_txt}" >&2
  exit 1
fi

output_dir=$(get-my-domino info | awk -F': ' '/^output_dir:/ { print $2; exit }')
if [[ -z "${output_dir}" ]]; then
  echo "error: unable to read output_dir from get-my-domino info" >&2
  exit 1
fi

if [[ -z "${audio_path}" ]]; then
  audio_path=$(uv run python - "${output_dir}" "${article_txt}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).expanduser().resolve()
txt = Path(sys.argv[2]).expanduser().resolve()
try:
    rel = txt.relative_to(root)
except ValueError as exc:
    raise SystemExit(f"article text is not under output_dir {root}: {txt}") from exc

if len(rel.parts) < 2:
    raise SystemExit(f"cannot infer audio path from article text: {txt}")

if rel.parts[0] == "la-settimana-di-domino":
    audio = root / "audio" / rel.parts[0] / f"{txt.parent.name}.m4a"
else:
    audio = root / "audio" / rel.parts[0] / f"{txt.stem}.m4a"

print(audio)
PY
)
fi

speech_txt=${article_txt%.txt}.speech.txt
timestamp=$(date +%Y%m%d-%H%M%S)
backup_audio=${audio_path%.m4a}.before-speech-normalize-"${timestamp}".m4a

echo "article: ${article_txt}"
echo "speech:  ${speech_txt}"
echo "audio:   ${audio_path}"

if [[ -f "${audio_path}" ]]; then
  cp "${audio_path}" "${backup_audio}"
  echo "backup:  ${backup_audio}"
  rm -f "${audio_path}"
  echo "removed existing audio so speak regenerates it"
else
  echo "backup:  skipped; existing audio not found"
fi

echo
echo "== Speech normalization diff =="
get-my-domino speech-normalize "${article_txt}" --speech-normalize-force --diff

echo
echo "== Audio regeneration =="
get-my-domino speak "${article_txt}" \
  --speech-normalize \
  --audio-format m4a \
  --audio-jobs 3 \
  --audio-timeout 1200

echo
echo "== Estimated timestamps for small text changes =="
uv run python - "${article_txt}" "${speech_txt}" "${audio_path}" <<'PY'
from pathlib import Path
import difflib
import re
import subprocess
import sys

txt = Path(sys.argv[1])
speech = Path(sys.argv[2])
audio = Path(sys.argv[3])

orig = txt.read_text(encoding="utf-8")
new = speech.read_text(encoding="utf-8")

duration = 0.0
if audio.exists():
    afinfo = subprocess.check_output(["afinfo", str(audio)], text=True)
    match = re.search(r"estimated duration: ([0-9.]+)", afinfo)
    if match:
        duration = float(match.group(1))

print(f"audio duration: {duration:.1f}s")
print("note: timestamps are character-position estimates, not forced alignment.")
print()

matcher = difflib.SequenceMatcher(None, orig, new)
printed = 0
for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag == "equal":
        continue

    old = orig[i1:i2]
    changed = new[j1:j2]
    if len(old) > 180 or len(changed) > 180:
        continue

    interesting_chars = set(",;:.!?()[]“”\"'«»–—\n")
    if not (set(old) & interesting_chars or set(changed) & interesting_chars):
        continue

    approx_sec = (len(orig[:i1]) / max(len(orig), 1)) * duration if duration else 0.0
    mm = int(approx_sec // 60)
    ss = int(approx_sec % 60)
    old_display = old.replace("\n", "\\n")
    changed_display = changed.replace("\n", "\\n")
    print(f"{mm:02d}:{ss:02d} {tag}")
    print(f"- {old_display!r}")
    print(f"+ {changed_display!r}")
    print()
    printed += 1

if printed == 0:
    print("No small punctuation/line-break changes found.")
PY

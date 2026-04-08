#!/usr/bin/env bash
set -euo pipefail

VOICE_NAME="${VOICE_STATUS_VOICE:-th-TH-PremwadeeNeural}"
PY_BIN="${PETBOX_PY_BIN:-/home/pj/ws/Petbox/.venv/bin/python}"

TEXT="$*"
if [[ -z "${TEXT// }" ]]; then
  exit 0
fi

if [[ ! -x "$PY_BIN" ]]; then
  echo "error: python not found: $PY_BIN" >&2
  exit 127
fi

if ! "$PY_BIN" -c "import edge_tts" >/dev/null 2>&1; then
  echo "error: missing edge-tts package in venv" >&2
  exit 127
fi

tmp_file="$(mktemp /tmp/petbox_status_tts_XXXXXX.mp3)"
cleanup() {
  rm -f "$tmp_file"
}
trap cleanup EXIT

"$PY_BIN" -m edge_tts --voice "$VOICE_NAME" --text "$TEXT" --write-media "$tmp_file" >/dev/null 2>&1

if command -v ffplay >/dev/null 2>&1; then
  ffplay -nodisp -autoexit -loglevel quiet "$tmp_file" >/dev/null 2>&1
  exit 0
fi

if command -v cvlc >/dev/null 2>&1; then
  cvlc --play-and-exit --quiet "$tmp_file" >/dev/null 2>&1
  exit 0
fi

echo "error: no mp3 player found (need ffplay or cvlc)" >&2
exit 127

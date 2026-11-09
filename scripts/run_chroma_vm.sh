#!/usr/bin/env bash
set -euo pipefail

CHROMA_HOME="${HOME}/chatbi-chroma"
CHROMA_BIN="${HOME}/chatbi-chroma-venv/bin/chroma"

mkdir -p "${CHROMA_HOME}/data" "${CHROMA_HOME}/logs"
exec "${CHROMA_BIN}" run \
  --path "${CHROMA_HOME}/data" \
  --host 0.0.0.0 \
  --port 8000 >>"${CHROMA_HOME}/logs/server.log" 2>&1

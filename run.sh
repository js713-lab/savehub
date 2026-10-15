#!/usr/bin/env bash
# One-command start: creates venv on first run, seeds samples, serves dashboard + MCP at http://127.0.0.1:${SAVEHUB_PORT:-8765} (/mcp)
set -e; cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
exec .venv/bin/python -m savehub.server   # dashboard + MCP (/mcp) on one port

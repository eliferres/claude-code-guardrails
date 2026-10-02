#!/usr/bin/env bash
# Takes a claim another session holds, and ledgers what that session was holding.
# usage: claims-takeover.sh <path> "<one-line reason>"
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails claims-takeover "$@"

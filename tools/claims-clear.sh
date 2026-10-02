#!/usr/bin/env bash
# Releases write claims. Wired to SessionEnd it clears the ending session; from the
# command line it clears whatever you name.
# usage: claims-clear.sh [--session <id>] [path...]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails claims-clear "$@"

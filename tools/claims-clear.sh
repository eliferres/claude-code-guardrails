#!/usr/bin/env bash
# Releases write claims. Wired to SessionEnd it clears the ending session; from the
# command line it clears whatever you name.
# usage: claims-clear.sh [--session <id>] [path...]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" claims-clear "$@"

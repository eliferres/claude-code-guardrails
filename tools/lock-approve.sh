#!/usr/bin/env bash
# Mints the batch-scoped approval token the file lock asks for.
# usage: lock-approve.sh "<batch label>" <path> [more paths...]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails lock-approve "$@"

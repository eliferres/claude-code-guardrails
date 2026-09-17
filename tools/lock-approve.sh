#!/usr/bin/env bash
# Mints the batch-scoped approval token the file lock asks for.
# usage: lock-approve.sh "<batch label>" <path> [more paths...]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" lock-approve "$@"

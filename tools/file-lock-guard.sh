#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|NotebookEdit. Refuses writes to protected paths
# unless a live approval token names the file.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" file-lock "$@"

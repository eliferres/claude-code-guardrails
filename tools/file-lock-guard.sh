#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|NotebookEdit. Refuses writes to protected paths
# unless a live approval token names the file.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails file-lock "$@"

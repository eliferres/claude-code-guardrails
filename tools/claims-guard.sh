#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|NotebookEdit. Refuses a file another live session
# claimed, so two concurrent sessions cannot silently overwrite each other.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" claims-guard "$@"

#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|MultiEdit|NotebookEdit. Refuses a file another live session
# claimed, so two concurrent sessions cannot silently overwrite each other.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails claims-guard "$@"

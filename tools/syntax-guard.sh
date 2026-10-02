#!/usr/bin/env bash
# PreToolUse hook on writes. Refuses a script write that would leave the file unparseable.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails syntax-guard "$@"

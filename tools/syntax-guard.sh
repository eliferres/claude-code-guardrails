#!/usr/bin/env bash
# PreToolUse hook on writes. Refuses a script write that would leave the file unparseable.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" syntax-guard "$@"

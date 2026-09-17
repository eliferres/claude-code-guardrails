#!/usr/bin/env bash
# PreToolUse hook on Bash. Refuses the command shapes listed in guardrails.json.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" command-guard "$@"

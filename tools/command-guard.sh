#!/usr/bin/env bash
# PreToolUse hook on Bash. Refuses the command shapes listed in guardrails.json.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails command-guard "$@"

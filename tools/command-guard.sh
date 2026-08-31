#!/usr/bin/env bash
# PreToolUse hook on Bash. Refuses the command shapes listed in guardrails.json.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" command-guard "$@"

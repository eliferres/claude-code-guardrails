#!/usr/bin/env bash
# Proves every installed guard can still go red. Run it in CI and on a schedule.
# usage: liveness.sh [--verbose]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" liveness "$@"

#!/usr/bin/env bash
# Proves every installed guard can still go red. Run it in CI and on a schedule.
# usage: liveness.sh [--verbose]
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$TOOLS${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m claude_code_guardrails liveness "$@"

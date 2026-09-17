#!/usr/bin/env bash
# Takes a claim another session holds, and ledgers what that session was holding.
# usage: claims-takeover.sh <path> "<one-line reason>"
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/claude_code_guardrails.py" claims-takeover "$@"

#!/usr/bin/env bash
# git pre-push hook. Refuses a push whose commits add credential-shaped text.
export GUARDRAILS_PROG="$0"   # so every hint names the script the reader ran
exec python3 "$(cd "$(dirname "$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")")" && pwd)/claude_code_guardrails.py" secret-scan "$@"

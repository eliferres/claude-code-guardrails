#!/usr/bin/env bash
# Mints the batch-scoped approval token the file lock asks for.
# usage: lock-approve.sh "<batch label>" <path> [more paths...]
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" lock-approve "$@"

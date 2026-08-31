#!/usr/bin/env bash
# Releases write claims. Wired to SessionEnd it clears the ending session; from the
# command line it clears whatever you name.
# usage: claims-clear.sh [--session <id>] [path...]
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" claims-clear "$@"

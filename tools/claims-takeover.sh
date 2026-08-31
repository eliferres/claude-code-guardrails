#!/usr/bin/env bash
# Takes a claim another session holds, and ledgers what that session was holding.
# usage: claims-takeover.sh <path> "<one-line reason>"
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" claims-takeover "$@"

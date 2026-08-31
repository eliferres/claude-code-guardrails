#!/usr/bin/env bash
# Proves every installed guard can still go red. Run it in CI and on a schedule.
# usage: liveness.sh [--verbose]
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" liveness "$@"

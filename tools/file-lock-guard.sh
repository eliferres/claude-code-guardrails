#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|NotebookEdit. Refuses writes to protected paths
# unless a live approval token names the file.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" file-lock "$@"

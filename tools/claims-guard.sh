#!/usr/bin/env bash
# PreToolUse hook on Write|Edit|NotebookEdit. Refuses a file another live session
# claimed, so two concurrent sessions cannot silently overwrite each other.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guardrails.py" claims-guard "$@"

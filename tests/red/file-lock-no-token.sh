#!/usr/bin/env bash
# RED CASE — the file lock must still refuse a protected write with no token.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
OUT="$(write_payload "$DIR/rules/team-rules.md" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *"lock-approve.sh"*) exit 0 ;; esac
echo "refusal did not name the mint command: $OUT"
exit 1

#!/usr/bin/env bash
# RED CASE — the command guard must still refuse blanket git staging.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
OUT="$(bash_payload "git add -A" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *"blanket staging"*) exit 0 ;; esac
echo "refusal did not name the shape: $OUT"
exit 1

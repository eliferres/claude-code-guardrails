#!/usr/bin/env bash
# RED CASE — the claims guard must still refuse a file another session is holding.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
TARGET="$DIR/notes/scratch.md"
write_payload "$TARGET" "session-alpha" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" >/dev/null 2>&1
OUT="$(write_payload "$TARGET" "session-beta" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *session-alpha*) exit 0 ;; esac
echo "refusal did not name the holder: $OUT"
exit 1

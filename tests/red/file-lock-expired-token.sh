#!/usr/bin/env bash
# RED CASE — the file lock must still refuse a token that has run out.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
TARGET="$DIR/rules/team-rules.md"
mkdir -p "$DIR/.guardrails"
printf 'batch: yesterday\nexpires: 1000000000\nfiles:\n%s\n' "$TARGET" \
  > "$DIR/.guardrails/lock-approval.token"
OUT="$(write_payload "$TARGET" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *expired*) exit 0 ;; esac
echo "refusal did not say the token expired: $OUT"
exit 1

#!/usr/bin/env bash
# RED CASE: the command guard must still refuse a shell redirect into a protected file.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
OUT="$(bash_payload_cwd "echo x > rules/team-rules.md" "$DIR" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *"shell-write-protected"*) exit 0 ;; esac
echo "refusal did not name the rule: $OUT"
exit 1

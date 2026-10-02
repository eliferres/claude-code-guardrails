#!/usr/bin/env bash
# RED CASE: the syntax guard must still refuse a shell file whose single-quoted
# python3 -c body is cut off by an apostrophe, which bash -n alone lets through.
set -u
cd "$(dirname "$0")/.." || exit 1
. ./fixture.sh
DIR="$(make_project)"
cat > "$DIR/content.sh" <<'BODY'
#!/usr/bin/env bash
python3 -c '
# the hook's input isn't trusted
print(1)
'
BODY
OUT="$(content_payload "$DIR/hook.sh" "$(cat "$DIR/content.sh")" | GUARDRAILS_PROJECT_DIR="$DIR" bash "$GUARD" 2>&1)"
RC=$?
rm -r "$DIR"
[ "$RC" -eq 2 ] || { echo "not blocked (exit $RC): $OUT"; exit 1; }
case "$OUT" in *"apostrophe"*) exit 0 ;; esac
echo "refusal did not name the apostrophe: $OUT"
exit 1

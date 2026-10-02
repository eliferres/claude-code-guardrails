"""Cross-session write claims: the guard, release and takeover."""

import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List

from .config import cli_config, die, guard_config, invocation, project_dir, state_dir, target_path
from .decisions import deny, hook_input
from .path_rules import matched_pattern


def claims_file() -> str:
    return os.path.join(state_dir(), "claims.tsv")


def read_claims(ttl: int) -> List[List[str]]:
    now = time.time()
    rows = []
    try:
        with open(claims_file()) as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3 and now - float(parts[0]) < ttl:
                    rows.append(parts)
    except (OSError, ValueError):
        pass
    return rows


def write_claims(rows: List[List[str]]) -> None:
    path = claims_file()
    temporary = "%s.tmp.%d" % (path, os.getpid())
    with open(temporary, "w") as handle:
        handle.write("".join("\t".join(row) + "\n" for row in rows[-500:]))
    os.replace(temporary, path)


def takeover_marker(path: str) -> str:
    return os.path.join(state_dir(), "takeover-" + hashlib.sha1(path.encode()).hexdigest()[:16])


def session_id(payload: Dict[str, Any]) -> str:
    return os.environ.get("GUARDRAILS_SESSION_ID") or payload.get("session_id") or "unknown"


def claims_guard() -> None:
    payload = hook_input()
    path = target_path(payload)
    root = project_dir()
    if not path or not path.startswith(root + os.sep):
        sys.exit(0)
    config = guard_config("claims")
    if matched_pattern(path, config.get("exempt") or [], root):
        sys.exit(0)

    ttl = int(config.get("ttl_seconds", 1800))
    mine = session_id(payload)
    rows = read_claims(ttl)
    holders = [row for row in rows if row[2] == path and row[1] != mine]

    marker = takeover_marker(path)
    granted = (os.path.exists(marker)
               and time.time() - os.path.getmtime(marker) < int(config.get("takeover_ttl_seconds", 600)))
    if holders and not granted:
        held_at, holder, _ = holders[-1]
        deny(
            "BLOCKED by claims-guard: another session is already writing %s.\n"
            "  holder: %s (claimed %d minutes ago)\n"
            "  Two sessions editing one file means the second write silently eats the first.\n"
            "  If that session is finished, release the claim:\n"
            '    %s --session "%s" "%s"\n'
            "  If your work outranks theirs, take it over — logged, and what they were holding\n"
            "  is written to the takeover ledger so it can be picked up rather than lost:\n"
            '    %s "%s" "<why yours wins>"'
            % (path, holder, int((time.time() - float(held_at)) // 60),
               invocation("claims-clear"), holder, path,
               invocation("claims-takeover"), path), "claimed-by-another-session")

    rows = [row for row in rows if not (row[1] == mine and row[2] == path)]
    rows.append(["%.0f" % time.time(), mine, path])
    write_claims(rows)
    sys.exit(0)


def claims_clear(argv: List[str]) -> None:
    who = os.environ.get("GUARDRAILS_SESSION_ID") or ""
    paths = []
    index = 0
    while index < len(argv):
        if argv[index] == "--session" and index + 1 < len(argv):
            who, index = argv[index + 1], index + 2
            continue
        paths.append(os.path.realpath(os.path.expanduser(argv[index])))
        index += 1

    if not who and not sys.stdin.isatty():
        # SessionEnd wiring: the session being cleared is the one in the hook payload.
        try:
            who = json.loads(sys.stdin.read() or "{}").get("session_id") or ""
        except Exception:
            who = ""
    if not who:
        die('usage: %s [--session <id>] [path...]\n' % invocation("claims-clear") +
            "        with no --session, the id comes from GUARDRAILS_SESSION_ID or a hook payload")

    config = cli_config("claims")
    rows = read_claims(int(config.get("ttl_seconds", 1800)))
    kept = [row for row in rows
            if not (row[1] == who and (not paths or row[2] in paths))]
    write_claims(kept)
    print("Released %d claim(s) held by %s." % (len(rows) - len(kept), who))


def claims_takeover(argv: List[str]) -> None:
    if len(argv) < 2:
        die('usage: %s <path> "<one-line reason>"\n' % invocation("claims-takeover") +
            "        the reason is the ledger entry a human reads later")
    path = os.path.realpath(os.path.expanduser(argv[0]))
    reason = argv[1]
    config = cli_config("claims")
    ttl = int(config.get("takeover_ttl_seconds", 600))

    rows = read_claims(int(config.get("ttl_seconds", 1800)))
    holders = [row for row in rows if row[2] == path]
    displaced = holders[-1] if holders else None

    with open(takeover_marker(path), "w") as handle:
        handle.write(reason + "\n")
    entry = {
        "ts": int(time.time()),
        "file": path,
        "reason": reason,
        "taken_by": os.environ.get("GUARDRAILS_SESSION_ID") or "cli",
        "displaced_session": displaced[1] if displaced else None,
        "displaced_claim_age_seconds": int(time.time() - float(displaced[0])) if displaced else None,
    }
    with open(os.path.join(state_dir(), "takeover-ledger.jsonl"), "a") as log:
        log.write(json.dumps(entry) + "\n")

    print("Takeover granted for %d minutes, this file only: %s" % (ttl // 60, path))
    print("Ledgered in .guardrails/takeover-ledger.jsonl — displaced session: %s"
          % (entry["displaced_session"] or "none"))
    print("Whatever that session had pending is now yours to carry, not to drop.")

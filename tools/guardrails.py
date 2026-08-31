#!/usr/bin/env python3
"""Deterministic guardrails for Claude Code agent setups.

One implementation, seven entry points. The `tools/*.sh` shims name them, so the
hook wiring in `.claude/settings.json` reads as one script per job:

    command-guard    PreToolUse on Bash    refuse dangerous command shapes
    file-lock        PreToolUse on writes  refuse protected paths without a token
    lock-approve     CLI                   mint a batch-scoped, expiring token
    claims-guard     PreToolUse on writes  refuse a file another session is holding
    claims-clear     CLI / SessionEnd      release claims
    claims-takeover  CLI                   take a claim, ledger what it displaced
    liveness         CLI / CI              prove every installed guard still blocks

Guards deny by exiting 2 with the reason on stderr — the PreToolUse contract that
blocks the tool call and hands the text back to the agent. Everything else exits 0.

Zero dependencies: Python 3.9+ standard library only.
"""

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DENY = 2


# ---------------------------------------------------------------- config + paths

def project_dir():
    for var in ("GUARDRAILS_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value:
            return os.path.realpath(os.path.expanduser(value))
    return KIT_ROOT


def config_path():
    value = os.environ.get("GUARDRAILS_CONFIG")
    if value:
        return os.path.realpath(os.path.expanduser(value))
    return os.path.join(project_dir(), "guardrails.json")


def state_dir():
    value = os.environ.get("GUARDRAILS_STATE_DIR")
    path = (os.path.realpath(os.path.expanduser(value)) if value
            else os.path.join(project_dir(), ".guardrails"))
    os.makedirs(path, exist_ok=True)
    return path


def guard_config(section):
    """Guards fail OPEN on a missing or broken config: a config typo must never brick
    the harness. `liveness` is the piece that notices a guard has stopped guarding."""
    try:
        with open(config_path()) as handle:
            return json.load(handle).get(section) or {}
    except Exception:
        sys.exit(0)


def cli_config(section):
    """CLI tools fail LOUD instead — a human is reading the output."""
    try:
        with open(config_path()) as handle:
            return json.load(handle).get(section) or {}
    except Exception as error:
        die("cannot read %s: %s" % (config_path(), error))


def die(message):
    sys.stderr.write("guardrails: %s\n" % message)
    sys.exit(1)


def deny(message):
    sys.stderr.write(message + "\n")
    sys.exit(DENY)


def hook_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)


def target_path(payload):
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return os.path.realpath(os.path.expanduser(raw)) if raw else ""


def matched_pattern(path, patterns, root):
    """fnmatch globs, absolute or relative to the project root. `*` crosses `/`."""
    relative = os.path.relpath(path, root)
    for pattern in patterns:
        absolute = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
        if fnmatch.fnmatch(path, os.path.expanduser(absolute)) or fnmatch.fnmatch(relative, pattern):
            return pattern
    return None


# ---------------------------------------------------------------- command guard

def command_guard():
    command = (hook_input().get("tool_input") or {}).get("command") or ""
    if not command.strip():
        sys.exit(0)
    flat = " ".join(command.split())
    config = guard_config("command_guard")
    allowlist = config.get("allowlist") or []

    for rule in config.get("rules") or []:
        if not re.search(rule["pattern"], flat):
            continue
        if any(entry.get("rule") == rule["id"] and re.fullmatch(entry.get("command", r"(?!)"), flat)
               for entry in allowlist):
            continue
        deny(
            "BLOCKED by command-guard [%s]: %s\n"
            "  command: %s\n"
            "  instead: %s\n"
            "  If this exact command is genuinely safe here, allowlist it in %s under\n"
            "  command_guard.allowlist: {\"rule\": \"%s\", \"command\": \"^...$\", \"why\": \"...\"}.\n"
            "  The pattern must match the whole command, so an allowlist entry frees one\n"
            "  command, never a shape."
            % (rule["id"], rule["blocks"], flat, rule["instead"], config_path(), rule["id"]))
    sys.exit(0)


# ---------------------------------------------------------------- protected-file lock

def token_file():
    return os.path.join(state_dir(), "lock-approval.token")


def read_token(path):
    """-> (batch, expires_epoch, [covered paths]) or None. A token with no file list
    covers nothing: an unscoped token is the hole this lock exists to close."""
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    batch, expires, covered, in_files = "", 0, [], False
    for line in lines:
        if in_files:
            if line.strip():
                covered.append(line.strip())
        elif line.startswith("batch: "):
            batch = line[7:]
        elif line.startswith("expires: "):
            expires = int(line[9:] or 0)
        elif line.strip() == "files:":
            in_files = True
    return (batch, expires, covered)


def token_covers(path, covered):
    for entry in covered:
        if entry.endswith("/") and path.startswith(entry):
            return True
        if entry == path:
            return True
    return False


def mint_hint(path):
    return 'tools/lock-approve.sh "<batch label>" "%s"' % path


def file_lock():
    path = target_path(hook_input())
    if not path:
        sys.exit(0)
    config = guard_config("file_lock")
    pattern = matched_pattern(path, config.get("protected") or [], project_dir())
    if not pattern:
        sys.exit(0)

    minutes = int(config.get("token_ttl_seconds", 1800)) // 60
    head = ("BLOCKED by file-lock-guard: %s is protected (it matches `%s` in %s).\n"
            % (path, pattern, config_path()))
    token = read_token(token_file())
    if token is None:
        deny(head +
             "  There is no approval token. Once a human has approved this change, mint one:\n"
             "    %s\n"
             "  A token names the files it covers and expires after %d minutes."
             % (mint_hint(path), minutes))
    batch, expires, covered = token
    if time.time() >= expires:
        deny(head + '  The token for batch "%s" expired %d minutes ago. Mint a fresh one:\n    %s'
             % (batch, int((time.time() - expires) // 60), mint_hint(path)))
    if not token_covers(path, covered):
        deny(head + '  The open token for batch "%s" does not cover this file. It covers:\n%s\n'
             "  Approval is per batch, so a live token from other work is not a yes for this\n"
             "  one. Mint a token that names this file:\n    %s"
             % (batch, "\n".join("    " + item for item in covered) or "    (nothing)",
                mint_hint(path)))
    sys.exit(0)


def lock_approve(argv):
    if len(argv) < 2:
        die('usage: lock-approve.sh "<batch label>" <path> [more paths...]\n'
            "        a token must name every file it approves — no files, no token")
    label, paths = argv[0], argv[1:]
    ttl = int(cli_config("file_lock").get("token_ttl_seconds", 1800))
    expires = int(time.time()) + ttl

    covered = []
    for raw in paths:
        resolved = os.path.realpath(os.path.expanduser(raw))
        covered.append(resolved + "/" if os.path.isdir(resolved) else resolved)

    path = token_file()
    with open(path, "w") as handle:
        handle.write("batch: %s\nexpires: %d\nfiles:\n%s\n"
                     % (label, expires, "\n".join(covered)))
    os.chmod(path, 0o600)
    with open(os.path.join(state_dir(), "lock-approvals.jsonl"), "a") as log:
        log.write(json.dumps({"ts": int(time.time()), "batch": label, "files": covered}) + "\n")

    print('Approval token minted for batch "%s" (%d minutes).' % (label, ttl // 60))
    for item in covered:
        print("  covers: %s" % item)


# ---------------------------------------------------------------- cross-session claims

def claims_file():
    return os.path.join(state_dir(), "claims.tsv")


def read_claims(ttl):
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


def write_claims(rows):
    path = claims_file()
    temporary = "%s.tmp.%d" % (path, os.getpid())
    with open(temporary, "w") as handle:
        handle.write("".join("\t".join(row) + "\n" for row in rows[-500:]))
    os.replace(temporary, path)


def takeover_marker(path):
    return os.path.join(state_dir(), "takeover-" + hashlib.sha1(path.encode()).hexdigest()[:16])


def session_id(payload):
    return os.environ.get("GUARDRAILS_SESSION_ID") or payload.get("session_id") or "unknown"


def claims_guard():
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
            '    tools/claims-clear.sh --session "%s" "%s"\n'
            "  If your work outranks theirs, take it over — logged, and what they were holding\n"
            "  is written to the takeover ledger so it can be picked up rather than lost:\n"
            '    tools/claims-takeover.sh "%s" "<why yours wins>"'
            % (path, holder, int((time.time() - float(held_at)) // 60), holder, path, path))

    rows = [row for row in rows if not (row[1] == mine and row[2] == path)]
    rows.append(["%.0f" % time.time(), mine, path])
    write_claims(rows)
    sys.exit(0)


def claims_clear(argv):
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
        die('usage: claims-clear.sh [--session <id>] [path...]\n'
            "        with no --session, the id comes from GUARDRAILS_SESSION_ID or a hook payload")

    config = cli_config("claims")
    rows = read_claims(int(config.get("ttl_seconds", 1800)))
    kept = [row for row in rows
            if not (row[1] == who and (not paths or row[2] in paths))]
    write_claims(kept)
    print("Released %d claim(s) held by %s." % (len(rows) - len(kept), who))


def claims_takeover(argv):
    if len(argv) < 2:
        die('usage: claims-takeover.sh <path> "<one-line reason>"\n'
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


# ---------------------------------------------------------------- gate liveness

def run_red_test(script, guard):
    """Red tests get the guard under test in $GUARD and nothing else from this process:
    an inherited GUARDRAILS_* variable would leak the caller's project into a test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GUARDRAILS_")}
    env["GUARD"] = guard
    try:
        return subprocess.run(["bash", script], env=env, timeout=60,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 124


def liveness(argv):
    """Always checks the kit it ships inside, so CI cannot be pointed at a friendlier copy."""
    verbose = "--verbose" in argv
    problems, lines = [], []
    manifest_path = os.path.join(KIT_ROOT, "guardrails.json")
    try:
        with open(manifest_path) as handle:
            manifest = json.load(handle).get("guards") or []
    except Exception as error:
        print("LIVENESS: cannot read %s (%s)" % (manifest_path, error))
        return 1
    if not manifest:
        problems.append("guardrails.json lists no guards — nothing is being proved")

    listed = {entry.get("script") for entry in manifest}
    tools = os.path.join(KIT_ROOT, "tools")
    for name in sorted(os.listdir(tools)):
        if name.endswith("-guard.sh") and "tools/" + name not in listed:
            problems.append("%s is installed but missing from the manifest — an unlisted guard is "
                            "one nobody is testing" % name)

    stub = os.path.join(KIT_ROOT, "tests", "noop-guard.sh")
    for entry in manifest:
        guard_id = entry.get("id", "?")
        script = os.path.join(KIT_ROOT, entry.get("script", ""))
        if not os.access(script, os.X_OK):
            problems.append("%s: %s is missing or not executable" % (guard_id, entry.get("script")))
            continue
        red_tests = entry.get("red_tests") or []
        if not red_tests:
            problems.append("%s: no red test — a guard nobody proves can still block is a guard "
                            "nobody can trust" % guard_id)
            continue
        for relative in red_tests:
            test = os.path.join(KIT_ROOT, relative)
            if not os.path.isfile(test):
                problems.append("%s: red test %s is missing" % (guard_id, relative))
                continue
            if run_red_test(test, script) != 0:
                problems.append("%s: red case %s did NOT block — the guard has gone quiet"
                                % (guard_id, relative))
            elif run_red_test(test, stub) == 0:
                problems.append("%s: red case %s passes with the guard stubbed out — it proves "
                                "nothing" % (guard_id, relative))
            elif verbose:
                lines.append("  red %s" % relative)
        lines.append("%-18s %d red case(s)" % (guard_id, len(red_tests)))

    for line in lines:
        print(line)
    if problems:
        print("\nLIVENESS: FAIL (%d problem(s))" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("\nLIVENESS: PASS — %d guard(s), every one still goes red on demand." % len(manifest))
    return 0


# ---------------------------------------------------------------- dispatch

COMMANDS = {
    "command-guard": lambda argv: command_guard(),
    "file-lock": lambda argv: file_lock(),
    "lock-approve": lock_approve,
    "claims-guard": lambda argv: claims_guard(),
    "claims-clear": claims_clear,
    "claims-takeover": claims_takeover,
    "liveness": liveness,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        die("usage: guardrails.py <%s> [args]" % "|".join(sorted(COMMANDS)))
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]) or 0)

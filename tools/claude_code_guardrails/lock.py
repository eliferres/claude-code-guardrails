"""The protected-file lock and the command that mints its approval token."""

import json
import os
import sys
import time
from typing import List, Optional, Tuple

from .config import cli_config, config_path, die, guard_config, invocation, project_dir, state_dir, target_path
from .decisions import deny, hook_input
from .path_rules import matched_pattern


def token_file() -> str:
    return os.path.join(state_dir(), "lock-approval.token")


def read_token(path: str) -> Optional[Tuple[str, int, List[str]]]:
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


def token_covers(path: str, covered: List[str]) -> bool:
    for entry in covered:
        if entry.endswith("/") and path.startswith(entry):
            return True
        if entry == path:
            return True
    return False


def mint_hint(path: str) -> str:
    return '%s "<batch label>" "%s"' % (invocation("lock-approve"), path)


def file_lock() -> None:
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
             % (mint_hint(path), minutes), "no-token")
    batch, expires, covered = token
    if time.time() >= expires:
        deny(head + '  The token for batch "%s" expired %d minutes ago. Mint a fresh one:\n    %s'
             % (batch, int((time.time() - expires) // 60), mint_hint(path)), "expired-token")
    if not token_covers(path, covered):
        deny(head + '  The open token for batch "%s" does not cover this file. It covers:\n%s\n'
             "  Approval is per batch, so a live token from other work is not a yes for this\n"
             "  one. Mint a token that names this file:\n    %s"
             % (batch, "\n".join("    " + item for item in covered) or "    (nothing)",
                mint_hint(path)), "token-does-not-cover")
    sys.exit(0)


def lock_approve(argv: List[str]) -> None:
    if len(argv) < 2:
        die('usage: %s "<batch label>" <path> [more paths...]\n'
            % invocation("lock-approve") +
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

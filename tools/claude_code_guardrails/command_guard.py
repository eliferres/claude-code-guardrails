"""The command guard: configured rules, the temp allowance, and shell writes."""

import os
import re
import sys
from typing import Dict, Optional

from .config import config_path, guard_config
from .decisions import deny, hook_input
from .path_rules import deletes_only_temp
from .shell import normalized_command
from .shell_write import shell_write_check


def safe_temp_delete(command: str, cwd: Optional[str]) -> bool:
    try:
        return deletes_only_temp(command, cwd)
    except ValueError:
        return False


# The command names a rule's pattern opens with: \brm\s+..., \b(curl|wget)\b...
RULE_COMMANDS = re.compile(r"(?:\\b)?\(?((?:[\w.-]+\|)*[\w.-]+)\)?(?:\\b|\\s)")


def names_ruled_command(rule: Dict[str, str], command: str) -> bool:
    """For a command the careful reading could not parse: does its raw text, with
    quotes and backslashes dropped, name a command the rule covers? A rule whose
    pattern does not open with command names counts as named, so an unreadable
    command is refused rather than passed."""
    match = RULE_COMMANDS.match(rule["pattern"])
    if not match:
        return True
    names = "|".join(re.escape(name) for name in match.group(1).split("|"))
    return bool(re.search(r"(?i)\b(%s)\b" % names, re.sub(r"[\\'\"]", "", command)))


def command_guard() -> None:
    payload = hook_input()
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        sys.exit(0)
    flat = " ".join(command.split())
    cwd = payload.get("cwd")
    cwd = os.path.realpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None
    config = guard_config("command_guard")
    allowlist = config.get("allowlist") or []
    # Each rule is matched against the command as written and in its normalized
    # spelling; the allowlist reads only the command as written. With no normalized
    # spelling the command could not be read, and the raw text alone would pass
    # `rm -r x -f` behind anything that breaks the reading.
    normalized = normalized_command(command)
    spellings = [flat] + ([normalized] if normalized else [])

    for rule in config.get("rules") or []:
        blocks = rule["blocks"]
        if not any(re.search(rule["pattern"], text) for text in spellings):
            if normalized is not None or not names_ruled_command(rule, command):
                continue
            blocks = ("a command the guard cannot read safely, naming a command this rule covers: "
                      + blocks)
        if rule.get("allow_in_temp") and safe_temp_delete(command, cwd):
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
            % (rule["id"], blocks, flat, rule["instead"], config_path(), rule["id"]),
            rule["id"])
    shell_write_check(command, flat, cwd)
    sys.exit(0)

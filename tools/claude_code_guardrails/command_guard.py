"""The command guard: configured rules, the temp allowance, and shell writes."""

import os
import re
import sys
from typing import Optional

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
    # spelling; the allowlist reads only the command as written.
    spellings = [flat] + [text for text in [normalized_command(command)] if text]

    for rule in config.get("rules") or []:
        if not any(re.search(rule["pattern"], text) for text in spellings):
            continue
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
            % (rule["id"], rule["blocks"], flat, rule["instead"], config_path(), rule["id"]),
            rule["id"])
    shell_write_check(command, flat, cwd)
    sys.exit(0)

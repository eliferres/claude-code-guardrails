"""Which files a shell command writes, and the check that keeps it off protected ones."""

import os
import re
from typing import FrozenSet, List, Optional

from .config import config_path, guard_config, project_dir
from .decisions import deny
from .path_rules import matched_pattern
from .shell import commands_in_folder, operands, reassigned_names, resolve_word


def copy_destinations(args: List[str], cwd: Optional[str], unreadable: FrozenSet[str]) -> List[str]:
    """Where a cp or mv lands: into -t DIR, else onto its last operand, which when it
    is a folder receives each source under its own name."""
    for index, arg in enumerate(args):
        if arg == "-t" and index + 1 < len(args):
            sources = operands(args[:index] + args[index + 2:])
            return [os.path.join(args[index + 1], os.path.basename(s.rstrip("/"))) for s in sources]
        if arg.startswith("--target-directory="):
            sources = operands([a for a in args if a != arg])
            return [os.path.join(arg.split("=", 1)[1], os.path.basename(s.rstrip("/"))) for s in sources]
    words = operands(args)
    if len(words) < 2:
        return []
    *sources, destination = words
    resolved = resolve_word(destination, cwd, unreadable)
    if destination.endswith("/") or (resolved and os.path.isdir(resolved)):
        return [os.path.join(destination, os.path.basename(s.rstrip("/"))) for s in sources]
    return [destination]


def written_paths(command: str, cwd: Optional[str]) -> List[str]:
    """Every file this command writes through a redirect, tee, sed -i, cp or mv,
    resolved to a real path."""
    unreadable = reassigned_names(command)
    found = []
    for name, args, redirects, folder in commands_in_folder(command, cwd):
        targets = [target for op, target in redirects
                   if ">" in op and not (op.endswith("&") and re.fullmatch(r"[0-9]+|-", target))]
        if name == "tee":
            targets += operands(args)
        elif name == "sed" and any(re.match(r"^-[a-zA-Z]*i|^--in-place", arg) for arg in args):
            targets += operands(args)
        elif name in ("cp", "mv"):
            targets += copy_destinations(args, folder, unreadable)
        for target in targets:
            path = resolve_word(target, folder, unreadable)
            if path and path != "/dev/null":
                found.append(path)
    return found


def shell_write_check(command: str, flat: str, cwd: Optional[str]) -> None:
    """The file lock sees the Write and Edit tools only, so the same protected paths
    are refused here when a shell command would write them."""
    protected = guard_config("file_lock").get("protected") or []
    if not protected:
        return
    try:
        paths = written_paths(command, cwd)
    except ValueError as error:
        # Unparseable means unreadable, not harmless: refuse it when it could write.
        if re.search(r">|\b(tee|sed|cp|mv)\b", command):
            deny("BLOCKED by command-guard [shell-write-protected]: this command could write a\n"
                 "  file and cannot be read safely (%s), so the guard cannot tell whether it\n"
                 "  touches a protected one.\n"
                 "  command: %s\n"
                 "  instead: fix the quoting, or make the change with the Write or Edit tool."
                 % (error, flat), "shell-write-protected")
        return
    root = project_dir()
    for path in paths:
        pattern = matched_pattern(path, protected, root)
        if pattern:
            deny(
                "BLOCKED by command-guard [shell-write-protected]: this command writes %s, a\n"
                "  protected file (it matches `%s` in %s).\n"
                "  command: %s\n"
                "  instead: make the change with the Write or Edit tool, where the file lock asks\n"
                "  for an approval token; a shell write would go around that check."
                % (path, pattern, config_path(), flat), "shell-write-protected")

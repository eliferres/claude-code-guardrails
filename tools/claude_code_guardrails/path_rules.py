"""Path matching: protected-path globs and the temp-folder delete allowance."""

import fnmatch
import glob
import os
import re
import shlex
import tempfile
from typing import List, Optional

from .shell import HEREDOC, commands_in_folder, operands, reassigned_names, resolve_word


def matched_pattern(path: str, patterns: List[str], root: str) -> Optional[str]:
    """fnmatch globs, absolute or relative to the project root. `*` crosses `/`."""
    relative = os.path.relpath(path, root)
    for pattern in patterns:
        absolute = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
        if fnmatch.fnmatch(path, os.path.expanduser(absolute)) or fnmatch.fnmatch(relative, pattern):
            return pattern
    return None


def temp_roots() -> List[str]:
    roots = {tempfile.gettempdir(), "/tmp", "/var/tmp", os.environ.get("TMPDIR") or "/tmp"}
    return sorted({os.path.realpath(root) for root in roots})


def deletes_only_temp(command: str, cwd: Optional[str], pattern: str) -> bool:
    """True when the command has at least one recursive rm, every path each one
    names resolves strictly inside a temp folder, and nothing else in the command
    matches the rule's pattern (`sh -c 'rm -rf ...'`, `find -exec rm -rf`). A path
    that cannot be resolved, the temp folder itself, a `..` or a symlink that leads
    out all count as outside; so does a glob with any match outside. A heredoc, a
    command substitution, a subshell or a zsh glob qualifier (`w(:h:h)` drops path
    parts) can change what gets deleted in a way this reading cannot follow, so a
    heredoc, any bracket or a backtick keeps the refusal."""
    if HEREDOC.search(command) or re.search(r"[()`]", command):
        return False
    roots = temp_roots()
    unreadable = reassigned_names(command)

    def inside(path: str) -> bool:
        return any(path.startswith(root + os.sep) for root in roots)

    found, rest = False, []
    for name, args, _, folder in commands_in_folder(command, cwd):
        split = args.index("--") if "--" in args else len(args)
        options, targets = args[:split], operands(args[:split]) + args[split + 1:]
        if name != "rm" or not any(re.match(r"^-[a-zA-Z]*[rR]|^--recursive$", arg) for arg in options):
            rest.append(" ".join(shlex.quote(word) for word in [name] + args))
            continue
        found = True
        for target in targets:
            path = resolve_word(target, folder, unreadable)
            if not path or not inside(path):
                return False
            if any(not inside(os.path.realpath(match)) for match in glob.glob(path)):
                return False
    return found and not re.search(pattern, " ; ".join(rest))

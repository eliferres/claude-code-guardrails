"""Path matching: protected-path globs and the temp-folder delete allowance."""

import fnmatch
import glob
import os
import re
import tempfile
from typing import List, Optional

from .shell import HEREDOC, commands_in_folder, folder_after, operands, reassigned_names, resolve_word


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


def deletes_only_temp(command: str, cwd: Optional[str]) -> bool:
    """True when the command is nothing but rm commands and cds into temp, and every
    path each rm names resolves strictly inside a temp folder. Paths are resolved
    before the command runs, so any other command in the line (ln -s, mv, a
    script) could change what a temp path points at by the time rm reaches it;
    that is why one keeps the refusal. A path that cannot be resolved, the temp
    folder itself, a `..` or a symlink that leads out all count as outside; so does
    a glob with any match outside. A heredoc, a command substitution, a subshell or
    a zsh glob qualifier (`w(:h:h)` drops path parts) can change what gets deleted
    in a way this reading cannot follow, so a heredoc, any bracket or a backtick
    keeps the refusal."""
    if HEREDOC.search(command) or re.search(r"[()`]", command):
        return False
    roots = temp_roots()
    unreadable = reassigned_names(command)

    def inside(path: Optional[str], or_root: bool = False) -> bool:
        return bool(path) and any(path.startswith(root + os.sep) or (or_root and path == root)
                                  for root in roots)

    found = False
    for name, args, _, folder in commands_in_folder(command, cwd):
        if name == "cd":
            if not inside(folder_after(name, args, folder, unreadable), or_root=True):
                return False
            continue
        if name != "rm":
            return False
        found = True
        split = args.index("--") if "--" in args else len(args)
        for target in operands(args[:split]) + args[split + 1:]:
            path = resolve_word(target, folder, unreadable)
            if not inside(path):
                return False
            if any(not inside(os.path.realpath(match)) for match in glob.glob(path)):
                return False
    return found

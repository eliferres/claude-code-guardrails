"""Which files a shell command writes, and the check that keeps it off protected ones."""

import glob
import os
import re
from typing import FrozenSet, List, Optional, Tuple

from .config import config_path, guard_config, project_dir
from .decisions import deny
from .path_rules import matched_pattern, path_matches
from .shell import brace_expansions, commands_in_folder, folder_after, operands, reassigned_names, resolve_word


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


def written_paths(command: str, cwd: Optional[str]) -> Tuple[List[str], List[str]]:
    """Every file this command writes through a redirect, tee, sed -i, cp or mv,
    after brace expansion: (the ones resolved to a real path, the relative ones
    written in a folder the guard lost track of, as written). A relative one of
    those is also resolved in the last folder the guard knew, the likeliest place
    it lands, so it is in both lists."""
    unreadable = reassigned_names(command)
    found, unplaced = [], []
    last_known = cwd
    for name, args, redirects, folder in commands_in_folder(command, cwd):
        place = folder if folder is not None else last_known
        if folder is not None:
            last_known = folder_after(name, args, folder, unreadable) or folder
        args = [word for arg in args for word in brace_expansions(arg)]
        targets = [word for op, target in redirects
                   if ">" in op and not (op.endswith("&") and re.fullmatch(r"[0-9]+|-", target))
                   for word in brace_expansions(target)]
        if name == "tee":
            targets += operands(args)
        elif name == "sed" and any(re.match(r"^-[a-zA-Z]*i|^--in-place", arg) for arg in args):
            targets += operands(args)
        elif name in ("cp", "mv"):
            targets += copy_destinations(args, place, unreadable)
        for target in targets:
            path = resolve_word(target, place, unreadable)
            if path and path != "/dev/null":
                found.append(path)
            if folder is None and not re.match(r"[/~]", target) and not re.search(r"[$`]", target):
                unplaced.append(target)
    return found, unplaced


def protected_match(path: str, protected: List[str], root: str) -> Optional[str]:
    """The protected pattern a write target hits. A target holding * ? or [ is a glob
    the shell expands first: it hits a protected pattern when any file it matches
    now does, or when a literal protected path would match it later."""
    pattern = matched_pattern(path, protected, root)
    if pattern or not re.search(r"[*?[]", path):
        return pattern
    for match in glob.glob(path):
        pattern = matched_pattern(os.path.realpath(match), protected, root)
        if pattern:
            return pattern
    for pattern in protected:
        literal = os.path.expanduser(pattern if os.path.isabs(pattern) else os.path.join(root, pattern))
        if not re.search(r"[*?[]", literal) and path_matches(literal, path):
            return pattern
    return None


def unplaced_match(target: str, protected: List[str]) -> Optional[str]:
    """The protected pattern a relative target written in an unknown folder could
    hit. That folder could be any folder, so the target hits every pattern whose
    last part its name matches. A pattern whose last part is a bare * (tools/*)
    names a folder, not a file name, so there the target must name that folder
    too (tools/x.py, ../tools/x.py)."""
    written = re.sub(r"^(\./)+", "", target.rstrip("/"))
    name = os.path.basename(written)
    for pattern in protected:
        last = os.path.basename(pattern)
        if re.fullmatch(r"\*+", last) and os.path.basename(os.path.dirname(pattern)):
            folder = os.path.basename(os.path.dirname(pattern)) + "/*"
            if path_matches(written, folder) or path_matches(written, "*/" + folder):
                return pattern
        elif path_matches(name, last) or (re.search(r"[*?[]", name) and path_matches(last, name)):
            return pattern
    return None


def refuse_write(what: str, pattern: str, flat: str) -> None:
    deny(
        "BLOCKED by command-guard [shell-write-protected]: this command writes %s\n"
        "  (it matches `%s` in %s).\n"
        "  command: %s\n"
        "  instead: make the change with the Write or Edit tool, where the file lock asks\n"
        "  for an approval token; a shell write would go around that check."
        % (what, pattern, config_path(), flat), "shell-write-protected")


def shell_write_check(command: str, flat: str, cwd: Optional[str]) -> None:
    """The file lock sees the Write and Edit tools only, so the same protected paths
    are refused here when a shell command would write them."""
    protected = guard_config("file_lock").get("protected") or []
    if not protected:
        return
    try:
        paths, unplaced = written_paths(command, cwd)
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
        pattern = protected_match(path, protected, root)
        if pattern:
            refuse_write("%s, a protected file" % path, pattern, flat)
    for target in unplaced:
        pattern = unplaced_match(target, protected)
        if pattern:
            refuse_write("%s in a folder the guard lost track of (after a branch, a loop,\n"
                         "  a brace group, pushd, eval or source), where it could be a protected file"
                         % target, pattern, flat)

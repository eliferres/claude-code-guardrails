"""git commands in one spelling, so a rule written for `git push --force origin main`
also sees `git -c k=v push -fu origin +main`."""

import re
from typing import List

# git's global options that take the next word as their value.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
                     "--config-env", "--exec-path", "--attr-source"}


def canonical_git_args(args: List[str]) -> List[str]:
    """git's arguments with the global options before the subcommand dropped. For
    push, every way of forcing becomes one `--force`: -f, a short cluster holding f
    (-fu), and a refspec starting with + (which forces that ref alone). The
    refspec keeps its name without the +, so a rule can still see it is main.
    --force-with-lease is left as written: it is the safe form."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in GIT_VALUE_OPTIONS else 1
    if index >= len(args):
        return []
    subcommand, rest = args[index], args[index + 1:]
    if subcommand != "push":
        return [subcommand] + rest
    force, kept = False, []
    for arg in rest:
        if arg in ("-f", "--force"):
            force = True
        elif re.fullmatch(r"-[A-Za-z]+", arg) and "f" in arg:
            force = True
            kept.append("-" + arg[1:].replace("f", ""))
        elif arg.startswith("+") and len(arg) > 1:
            force = True
            kept.append(arg[1:])
        else:
            kept.append(arg)
    return ["push"] + (["--force"] if force else []) + kept

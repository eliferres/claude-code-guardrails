"""Dispatch from a job name to its implementation."""

import random
import sys
from typing import Any, List, NoReturn, Optional

from . import __version__
from .claims import claims_clear, claims_guard, claims_takeover
from .command_guard import command_guard
from .config import PROG, die
from .decisions import RUN, log_decision, sample_allow_every
from .liveness import liveness
from .lock import file_lock, lock_approve
from .secret_scan import secret_scan
from .syntax import syntax_guard


COMMANDS = {
    "command-guard": lambda argv: command_guard(),
    "file-lock": lambda argv: file_lock(),
    "syntax-guard": lambda argv: syntax_guard(),
    "secret-scan": secret_scan,
    "lock-approve": lock_approve,
    "claims-guard": lambda argv: claims_guard(),
    "claims-clear": claims_clear,
    "claims-takeover": claims_takeover,
    "liveness": liveness,
}


GUARDS = ("command-guard", "file-lock", "syntax-guard", "claims-guard")


def run_guard(job: str, argv: List[str]) -> NoReturn:
    """Runs a guard and logs one allowed call in N. Refusals log themselves in deny()."""
    RUN["guard"] = job
    try:
        COMMANDS[job](argv)
        code: Any = 0
    except SystemExit as done:
        code = done.code
    if code in (0, None):
        every = sample_allow_every()
        if every > 0 and random.randrange(every) == 0:
            log_decision("allow", None)
    sys.exit(code)


def main(argv: Optional[List[str]] = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--version"]:
        print("claude-code-guardrails %s" % __version__)
        sys.exit(0)
    if not args or args[0] not in COMMANDS:
        die("usage: %s <%s> [args]" % (PROG, "|".join(sorted(COMMANDS))))
    if args[0] in GUARDS:
        run_guard(args[0], args[1:])
    sys.exit(COMMANDS[args[0]](args[1:]) or 0)

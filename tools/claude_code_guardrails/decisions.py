"""The hook payload, refusals, and the decision log every guard writes to."""

import json
import os
import sys
import time
from typing import Any, Dict, NoReturn, Optional

from .config import DENY, PROG, config_path, state_dir


# The guard running and the payload it read, for the decision log.
RUN: Dict[str, Any] = {"guard": "", "payload": {}}
# One allowed call in this many is logged unless the config says otherwise: at a
# few hundred tool calls a day that is enough rows to see each guard's traffic
# within a day or two, and the log stays small. 0 turns sampling off.
DEFAULT_SAMPLE_ALLOW_EVERY = 10


def deny(message: str, rule: str) -> NoReturn:
    log_decision("deny", rule)
    sys.stderr.write(message + "\n")
    sys.exit(DENY)


def hook_input() -> Dict[str, Any]:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.exit(0)
    RUN["payload"] = payload
    return payload


def log_decision(decision: str, rule: Optional[str]) -> None:
    """One row in .guardrails/decisions.jsonl. The rows hold command text, so the
    file is readable by its owner only. Logging never changes a verdict: a row that
    cannot be written costs one warning line on stderr."""
    try:
        tool_input = RUN["payload"].get("tool_input") or {}
        subject = (tool_input.get("command") or tool_input.get("file_path")
                   or tool_input.get("notebook_path") or "")
        row = {"ts": int(time.time()), "guard": RUN["guard"], "decision": decision,
               "rule": rule, "subject": " ".join(str(subject).split())[:300]}
        handle = os.open(os.path.join(state_dir(), "decisions.jsonl"),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(handle, "a") as log:
            os.fchmod(handle, 0o600)  # also tightens a log made before this rule
            log.write(json.dumps(row) + "\n")
    except Exception as error:
        sys.stderr.write("%s: warning: decision log not written: %s\n" % (PROG, error))


def sample_allow_every() -> int:
    try:
        with open(config_path()) as handle:
            section = json.load(handle).get("decision_log") or {}
        return int(section.get("sample_allow_every", DEFAULT_SAMPLE_ALLOW_EVERY))
    except Exception:
        return DEFAULT_SAMPLE_ALLOW_EVERY

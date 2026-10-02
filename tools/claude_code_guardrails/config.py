"""Where the project, its config and its state live, and how a job names itself."""

import json
import os
import sys
from typing import Any, Dict, NoReturn


def kit_root() -> str:
    """A kit checkout keeps this package in tools/ beside guardrails.json. Installed
    it lives in site-packages, so the kit is the project the hook runs for."""
    tools = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    checkout = os.path.dirname(tools)
    if os.path.basename(tools) == "tools" and os.path.isfile(os.path.join(checkout, "guardrails.json")):
        return checkout
    for var in ("GUARDRAILS_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value:
            return os.path.realpath(os.path.expanduser(value))
    return os.path.realpath(os.getcwd())


KIT_ROOT = kit_root()
DENY = 2

# Each tools/*.sh wrapper exports its own $0, so a clone keeps naming the script the
# reader ran; installed there is no wrapper, and the console script's own name is the
# only thing they can type.
PROG = os.environ.get("GUARDRAILS_PROG") or os.path.basename(sys.argv[0])
if PROG == "__main__.py":  # python -m claude_code_guardrails
    PROG = "claude-code-guardrails"

JOB_SCRIPTS = {
    "command-guard": "command-guard.sh",
    "file-lock": "file-lock-guard.sh",
    "syntax-guard": "syntax-guard.sh",
    "secret-scan": "pre-push-secret-scan.sh",
    "lock-approve": "lock-approve.sh",
    "claims-guard": "claims-guard.sh",
    "claims-clear": "claims-clear.sh",
    "claims-takeover": "claims-takeover.sh",
    "liveness": "liveness.sh",
}


def invocation(job: str) -> str:
    """How to run one of the jobs, in the form the reader themself used."""
    if os.environ.get("GUARDRAILS_PROG"):
        return os.path.join(os.path.dirname(PROG), JOB_SCRIPTS[job])
    return "%s %s" % (PROG, job)


# ---------------------------------------------------------------- config + paths

def project_dir() -> str:
    for var in ("GUARDRAILS_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value:
            return os.path.realpath(os.path.expanduser(value))
    return KIT_ROOT


def config_path() -> str:
    value = os.environ.get("GUARDRAILS_CONFIG")
    if value:
        return os.path.realpath(os.path.expanduser(value))
    return os.path.join(project_dir(), "guardrails.json")


def state_dir() -> str:
    value = os.environ.get("GUARDRAILS_STATE_DIR")
    path = (os.path.realpath(os.path.expanduser(value)) if value
            else os.path.join(project_dir(), ".guardrails"))
    os.makedirs(path, exist_ok=True)
    return path


def guard_config(section: str) -> Dict[str, Any]:
    """Guards fail OPEN on a missing or broken config: a config typo must never brick
    the harness. `liveness` is the piece that notices a guard has stopped guarding.
    The warning keeps that fail-open visible to whoever reads the hook output."""
    path = config_path()
    try:
        with open(path) as handle:
            return json.load(handle).get(section) or {}
    except OSError as error:
        reason = error.strerror or str(error)  # the OSError text repeats the path
    except Exception as error:
        reason = str(error)
    sys.stderr.write("%s: warning: config %s: %s; the guard is letting this call through\n"
                     % (PROG, path, reason))
    sys.exit(0)


def cli_config(section: str) -> Dict[str, Any]:
    """CLI tools fail LOUD instead — a human is reading the output."""
    try:
        with open(config_path()) as handle:
            return json.load(handle).get(section) or {}
    except Exception as error:
        die("cannot read %s: %s" % (config_path(), error))


def die(message: str) -> NoReturn:
    sys.stderr.write("%s: %s\n" % (PROG, message))
    sys.exit(1)


def target_path(payload: Dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return os.path.realpath(os.path.expanduser(raw)) if raw else ""

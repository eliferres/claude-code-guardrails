"""Liveness: prove every guard in the manifest still goes red."""

import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from .config import DENY, KIT_ROOT, PROG


def run_red_test(script: str, guard: str) -> int:
    """Red tests get the guard under test in $GUARD and nothing else from this process:
    an inherited GUARDRAILS_* variable would leak the caller's project into a test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GUARDRAILS_")}
    env["GUARD"] = guard
    try:
        return subprocess.run(["bash", script], env=env, timeout=60,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except subprocess.TimeoutExpired:
        return 124


def _read_manifest(manifest_path: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """-> (guards, None) on success, or (None, error text) — liveness turns the error
    into the same FAIL message the old inline try/except printed."""
    try:
        with open(manifest_path) as handle:
            return json.load(handle).get("guards") or [], None
    except Exception as error:
        return None, str(error)


def _unlisted_guard_problems(manifest: List[Dict[str, Any]], tools_dir: str) -> List[str]:
    """An installed *-guard.sh the manifest doesn't list is one liveness never exercises."""
    listed = {entry.get("script") for entry in manifest}
    problems = []
    for name in sorted(os.listdir(tools_dir)):
        if name.endswith("-guard.sh") and "tools/" + name not in listed:
            problems.append("%s is installed but missing from the manifest — an unlisted guard is "
                            "one nobody is testing" % name)
    return problems


def _check_guard(entry: Dict[str, Any], stub: str, verbose: bool) -> Tuple[List[str], List[str]]:
    """Runs one guard's red tests against itself and against the no-op stub, so a
    passing red case proves the guard blocked it rather than proving nothing."""
    problems, lines = [], []
    guard_id = entry.get("id", "?")
    script = os.path.join(KIT_ROOT, entry.get("script", ""))
    if not os.access(script, os.X_OK):
        problems.append("%s: %s is missing or not executable" % (guard_id, entry.get("script")))
        return problems, lines
    red_tests = entry.get("red_tests") or []
    if not red_tests:
        problems.append("%s: no red test — a guard nobody proves can still block is a guard "
                        "nobody can trust" % guard_id)
        return problems, lines
    for relative in red_tests:
        test = os.path.join(KIT_ROOT, relative)
        if not os.path.isfile(test):
            problems.append("%s: red test %s is missing" % (guard_id, relative))
            continue
        if run_red_test(test, script) != 0:
            problems.append("%s: red case %s did NOT block — the guard has gone quiet"
                            % (guard_id, relative))
        elif run_red_test(test, stub) == 0:
            problems.append("%s: red case %s passes with the guard stubbed out — it proves "
                            "nothing" % (guard_id, relative))
        elif verbose:
            lines.append("  red %s" % relative)
    lines.append("%-18s %d red case(s)" % (guard_id, len(red_tests)))
    return problems, lines


def _print_liveness_report(lines: List[str], problems: List[str], guard_count: int) -> int:
    for line in lines:
        print(line)
    if problems:
        print("\nLIVENESS: FAIL (%d problem(s))" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("\nLIVENESS: PASS — %d guard(s), every one still goes red on demand." % guard_count)
    return 0


def liveness(argv: List[str]) -> int:
    """Always checks the kit it ships inside, so CI cannot be pointed at a friendlier copy."""
    verbose = "--verbose" in argv
    needed = ("guardrails.json", "tools", os.path.join("tests", "noop-guard.sh"))
    missing = [name for name in needed if not os.path.exists(os.path.join(KIT_ROOT, name))]
    if missing:
        sys.stderr.write("%s: %s has no %s; run it inside a kit checkout\n"
                         % (PROG, KIT_ROOT, ", ".join(missing)))
        return DENY
    manifest_path = os.path.join(KIT_ROOT, "guardrails.json")
    manifest, error = _read_manifest(manifest_path)
    if error is not None:
        print("LIVENESS: cannot read %s (%s)" % (manifest_path, error))
        return 1

    problems, lines = [], []
    if not manifest:
        problems.append("guardrails.json lists no guards — nothing is being proved")
    problems.extend(_unlisted_guard_problems(manifest, os.path.join(KIT_ROOT, "tools")))

    stub = os.path.join(KIT_ROOT, "tests", "noop-guard.sh")
    for entry in manifest:
        guard_problems, guard_lines = _check_guard(entry, stub, verbose)
        problems.extend(guard_problems)
        lines.extend(guard_lines)

    return _print_liveness_report(lines, problems, len(manifest))

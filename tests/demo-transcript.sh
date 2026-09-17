#!/usr/bin/env bash
# The demo receipt, checked against a real run. `replay` runs every command in
# demo/transcript.json inside a throwaway copy of the repo and compares output
# and exit code byte for byte; `picture` checks every text row of
# demo/terminal.svg back to that transcript, so the image cannot drift either.
# Regenerate the transcript from a real run: UPDATE_DEMO_TRANSCRIPT=1 bash tests/demo-transcript.sh replay
# usage: demo-transcript.sh replay|picture
set -u
cd "$(dirname "$0")/.." || exit 1
MODE="${1:-replay}"

ROOT="$PWD" MODE="$MODE" python3 - <<'PY'
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.environ["ROOT"]
MODE = os.environ["MODE"]
TRANSCRIPT = os.path.join(ROOT, "demo", "transcript.json")
PICTURE = os.path.join(ROOT, "demo", "terminal.svg")
PLACEHOLDER = "/path/to/checkout"
SKIP = {".git", "__pycache__", ".guardrails", "build", "dist"}

with open(TRANSCRIPT) as handle:
    transcript = json.load(handle)


def fail(message):
    print(message)
    sys.exit(1)


def run_transcript():
    """-> list of {"cmd", "out", "status"} from a real run in a copy of the repo."""
    copy = os.path.join(tempfile.mkdtemp(prefix="guardrails-demo."), "checkout")
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(*SKIP, "*.egg-info"))
    env = dict(os.environ)
    env["GUARDRAILS_PROJECT_DIR"] = os.path.join(copy, "demo")  # the walkthrough's one export
    results = []
    try:
        for entry in transcript:
            done = subprocess.run(["bash", "-c", entry["cmd"]], cwd=copy, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = done.stdout.decode()
            # Only the copy's own path is machine-specific: macOS resolves /var to
            # /private/var, so both spellings have to go.
            for form in (os.path.realpath(copy), copy):
                out = out.replace(form, PLACEHOLDER)
            results.append({"cmd": entry["cmd"], "out": out.rstrip("\n"), "status": done.returncode})
    finally:
        shutil.rmtree(os.path.dirname(copy), ignore_errors=True)
    return results


def replay():
    results = run_transcript()
    if os.environ.get("UPDATE_DEMO_TRANSCRIPT") == "1":
        with open(TRANSCRIPT, "w") as handle:
            json.dump(results, handle, indent=2)
            handle.write("\n")
        print("rewrote %s from a real run" % TRANSCRIPT)
        return
    if len(results) != len(transcript):
        fail("ran %d commands, the transcript holds %d" % (len(results), len(transcript)))
    for index, (expected, actual) in enumerate(zip(transcript, results), start=1):
        for field in ("out", "status"):
            if expected[field] != actual[field]:
                fail("entry %d (%s)\n        expected %s: %r\n        actual   %s: %r"
                     % (index, expected["cmd"], field, expected[field], field, actual[field]))


def svg_rows():
    """Every text row of the picture below the title bar, in order, indentation kept."""
    root = ET.parse(PICTURE).getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    rows = []
    for element in root.findall("svg:text", namespace):
        y = element.get("y")
        if y is None or int(y) < 40:
            continue  # the chrome label in the title bar, not session text
        spans = element.findall("svg:tspan", namespace)
        rows.append(spans[-1].text or "" if spans else element.text or "")
    return rows


def is_command_row(row):
    """A command row is a run of whole words from some cmd: the renderer wraps a long
    command on spaces, ending a wrapped row with ` \\` and indenting what follows."""
    text = row[4:] if row.startswith("    ") else row
    text = text[:-2] if text.endswith(" \\") else text
    words = text.split(" ")
    for entry in transcript:
        parts = entry["cmd"].split(" ")
        if any(parts[i:i + len(words)] == words for i in range(len(parts))):
            return True
    return False


def is_output_row(row):
    head = row[:-1] if row.endswith("…") else row
    for entry in transcript:
        for line in entry["out"].splitlines():
            if line.startswith(head):
                return True
    return False


def picture():
    rows = svg_rows()
    if not rows:
        fail("%s has no session rows" % PICTURE)
    for index, row in enumerate(rows, start=1):
        if not is_command_row(row) and not is_output_row(row):
            fail("row %d of the picture is in neither the commands nor the output "
                 "of the transcript: %r" % (index, row))


{"replay": replay, "picture": picture}[MODE]()
PY

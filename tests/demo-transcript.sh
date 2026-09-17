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
    """The session rows of the picture, in order, as (kind, text).

    kind is "prompt" for the row a command starts on, "cont" for the rest of a
    wrapped command, "out" for a line of output. Nothing here keys on a
    coordinate or a row count: the only text that is not session text is the
    window's own label, and it is the only one carrying a font-size of its own.
    """
    root = ET.parse(PICTURE).getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    rows = []
    for element in root.findall("svg:text", namespace):
        if element.get("font-size"):
            continue  # the label in the window chrome, not session text
        spans = element.findall("svg:tspan", namespace)
        if spans:
            rows.append(("prompt", spans[-1].text or ""))
        elif element.get("class") == "cmd":
            # a wrapped command's later rows are indented; the command itself is
            # split on spaces, so the indentation is the renderer's, not the text's
            rows.append(("cont", (element.text or "").lstrip()))
        else:
            rows.append(("out", element.text or ""))
    return rows


def shows(row, line):
    """A row shows an output line whole, or end-trimmed with exactly one ellipsis."""
    if row == line:
        return True
    head = row[:-1]
    return (row.endswith("…") and row.count("…") == 1
            and len(head) < len(line) and line.startswith(head))


def picture():
    """Every entry the picture shows, it shows whole and in order: the command
    rebuilt exactly, then each of its output lines as its own row. It may stop
    between commands, and no row may be left over."""
    rows = svg_rows()
    if not rows:
        fail("%s has no session rows" % PICTURE)
    index = 0
    for number, entry in enumerate(transcript, start=1):
        if index == len(rows):
            return  # stopping at a command boundary is the one allowed cut
        kind, text = rows[index]
        if kind != "prompt":
            fail("row %d should open command %d (%s), and is %s: %r"
                 % (index + 1, number, entry["cmd"], kind, text))
        chunks = [text]
        index += 1
        while chunks[-1].endswith(" \\"):
            if index == len(rows) or rows[index][0] != "cont":
                fail("command %d breaks off after row %d: %r" % (number, index, chunks[-1]))
            chunks.append(rows[index][1])
            index += 1
        rebuilt = " ".join(chunk[:-2] if chunk.endswith(" \\") else chunk for chunk in chunks)
        if rebuilt != entry["cmd"]:
            fail("the rows for command %d rebuild to\n        %r\n        the transcript has\n        %r"
                 % (number, rebuilt, entry["cmd"]))
        for line in entry["out"].splitlines():
            if not line.strip():
                continue  # the renderer drops empty rows
            if index == len(rows):
                fail("the picture stops inside the output of command %d, missing: %r"
                     % (number, line))
            kind, text = rows[index]
            if kind != "out" or not shows(text, line):
                fail("row %d of the picture is %s: %r\n        output line %d of command "
                     "%d is: %r" % (index + 1, kind, text, index + 1, number, line))
            index += 1
    if index != len(rows):
        fail("%d row(s) of the picture are in no transcript entry, from row %d: %r"
             % (len(rows) - index, index + 1, rows[index][1]))


{"replay": replay, "picture": picture}[MODE]()
PY

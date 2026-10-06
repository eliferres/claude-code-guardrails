"""The syntax guard: parse a script as a Write or Edit would leave it."""

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from .config import guard_config, project_dir, target_path
from .decisions import deny, hook_input
from .path_rules import matched_pattern
from .shell import Heredoc, ends_heredoc, heredoc_openers


# `python3 -c '` followed by its body, with any flags (and one flag value) between.
PYTHON_C = re.compile(r"python(?:3(?:\.[0-9]+)?)?\s+(?:-[A-Za-z]+(?:\s+[^-\s'\"]\S*)?\s+)*-c\s+'")


def python_error(source: str) -> Optional[SyntaxError]:
    try:
        ast.parse(source)
        return None
    except SyntaxError as error:
        return error


def embedded_python_error(body: str) -> Optional[SyntaxError]:
    error = python_error(body)
    # A body inside a double-quoted shell string (an eval or ssh argument, or test
    # data) reaches Python only after the shell drops its backslash escapes, so it
    # gets a second reading that way before it counts as broken.
    if error and '\\"' in body:
        unescaped = body.replace('\\"', '"').replace("\\$", "$").replace("\\\\", "\\")
        if python_error(unescaped) is None:
            return None
    return error


def heredoc_bodies(script: str) -> List[Tuple[int, int, str, Heredoc]]:
    """-> [(body start, body end, the opener line up to its <<, the opener)],
    read line by line as the shell does, so an opener inside a body is body text."""
    bodies = []
    pending: List[Tuple[Heredoc, str]] = []
    start, offset = None, 0
    for line in script.split("\n"):
        if pending:
            match, opener = pending[0]
            if start is None:
                start = offset
            if ends_heredoc(line, match):
                bodies.append((start, offset, opener[:match.start], match))
                pending.pop(0)
                start = None
        else:
            pending = [(match, line) for match in heredoc_openers(line)]
        offset += len(line) + 1
    return bodies


def embedded_python_problem(script: str) -> Optional[str]:
    """Python inside a shell file that will not run as written. bash -n cannot see
    it: to bash a single-quoted body is just a string, and an apostrophe in it ends
    that string early. With an even quote count the file still passes bash -n and
    the Python runs cut off at the apostrophe."""
    def line_of(position: int) -> int:
        return script.count("\n", 0, position) + 1

    def commented_out(position: int) -> bool:
        start = script.rfind("\n", 0, position) + 1
        return script[start:position].lstrip().startswith("#")

    # The two legal ways to write an apostrophe inside single quotes ('\'' and
    # '"'"') read as one ordinary character, as the shell would join them.
    script = script.replace("'\"'\"'", "_").replace("'\\''", "_")
    bodies = heredoc_bodies(script)
    for match in PYTHON_C.finditer(script):
        end = script.find("'", match.end())
        # Inside any heredoc body the text is data for that body's reader.
        if (commented_out(match.start()) or end < 0
                or any(start <= match.start() < stop for start, stop, _, _ in bodies)):
            continue
        body = script[match.end():end]
        last_line = body.rsplit("\n", 1)[-1]
        glued = script[end + 1:end + 2]
        # A body that really ended here is followed by a space, a newline or an
        # operator. A letter means the quote was an apostrophe inside a word; a
        # comment on the last line means the quote sat inside that comment.
        if (glued.isalnum() or glued == "_") or re.search(r"#[^\n]*[A-Za-z]", last_line):
            return ("line %d: an apostrophe inside the single-quoted python3 -c body ends the "
                    "shell string early, so Python would run only up to %r. Reword it without "
                    "the apostrophe, or move the Python into a quoted heredoc."
                    % (line_of(end), last_line.strip()[-40:]))
        error = embedded_python_error(body)
        if error:
            return ("the python3 -c body opening at line %d does not compile (python line %s: %s)"
                    % (line_of(match.start()), error.lineno, error.msg))
    for start, stop, opener, match in bodies:
        # Only a quoted tag fed to Python: an unquoted one is expanded by the shell first.
        if not match.quoted or not re.search(r"\bpython(?:3(?:\.[0-9]+)?)?\b", opener) or commented_out(start - 1):
            continue
        body = script[start:stop]
        if match.strip_tabs:
            body = re.sub(r"(?m)^\t+", "", body)
        error = embedded_python_error(body)
        if error:
            return ("the Python in heredoc %s opening at line %d does not compile (python line %s: %s)"
                    % (match.delimiter, line_of(start - 1), error.lineno, error.msg))
    return None


def script_kind(path: str, text: str) -> Optional[str]:
    """"bash", "zsh", "py", or None for a file this guard does not parse. A zsh
    shebang wins over a .sh name: zsh syntax such as glob qualifiers is not bash."""
    extension = os.path.splitext(path)[1]
    first = text.split("\n", 1)[0] if text.startswith("#!") else ""
    if re.search(r"\bzsh\b", first) or extension == ".zsh":
        return "zsh"
    if extension in (".sh", ".bash"):
        return "bash"
    if extension == ".py" or "python" in first:
        return "py"
    if re.search(r"\b(ba)?sh\b", first):
        return "bash"
    return None


def syntax_problem(path: str, text: str, kind: str) -> Optional[str]:
    if kind == "py":
        error = python_error(text)
        return "python line %s: %s" % (error.lineno, error.msg) if error else None
    if not shutil.which(kind):
        return None  # no zsh here to ask; better silent than parsed as the wrong shell
    handle, scratch = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        result = subprocess.run([kind, "-n", scratch], capture_output=True, text=True, timeout=10)
    finally:
        os.remove(scratch)
    if result.returncode != 0:
        lines = result.stderr.replace(scratch, path).strip().splitlines()
        return "%s -n: %s" % (kind, " ".join(lines[-3:]))
    return embedded_python_problem(text)


def file_after(payload: Dict[str, Any], path: str) -> Optional[str]:
    """The text the file would hold once this Write, Edit or MultiEdit lands, or None when the
    tool itself will refuse the call (old_string missing, or found more than once
    without replace_all), which is that tool's refusal to make, not this guard's."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    edits = tool_input.get("edits") if tool == "MultiEdit" else [tool_input]
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        text = None
    for edit in edits or []:
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old == "":
            if text:
                return None
            text = new
            continue
        if text is None or old not in text or (text.count(old) > 1 and not edit.get("replace_all")):
            return None
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def syntax_guard() -> None:
    payload = hook_input()
    path = target_path(payload)
    if not path or payload.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)
    patterns = guard_config("syntax_check").get("paths") or []
    if not matched_pattern(path, patterns, project_dir()):
        sys.exit(0)
    text = file_after(payload, path)
    kind = script_kind(path, text) if text is not None else None
    if kind is None:
        sys.exit(0)
    problem = syntax_problem(path, text, kind)
    if problem:
        deny("BLOCKED by syntax-guard: this %s would leave %s unparseable.\n"
             "  %s\n"
             "  instead: make the change so the whole file parses after it. A hook that does not\n"
             "  parse fails on every call that runs it, including the call that would fix it."
             % (payload.get("tool_name"), path, problem), "unparseable")
    sys.exit(0)

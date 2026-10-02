#!/usr/bin/env python3
"""Deterministic guardrails for Claude Code agent setups.

One implementation, nine entry points. The `tools/*.sh` shims name them, so the
hook wiring in `.claude/settings.json` reads as one script per job:

    command-guard    PreToolUse on Bash    refuse dangerous command shapes
    file-lock        PreToolUse on writes  refuse protected paths without a token
    lock-approve     CLI                   mint a batch-scoped, expiring token
    syntax-guard     PreToolUse on writes  refuse a script write that would not parse
    secret-scan      git pre-push          refuse a push that adds a credential
    claims-guard     PreToolUse on writes  refuse a file another session is holding
    claims-clear     CLI / SessionEnd      release claims
    claims-takeover  CLI                   take a claim, ledger what it displaced
    liveness         CLI / CI              prove every installed guard still blocks

Guards deny by exiting 2 with the reason on stderr — the PreToolUse contract that
blocks the tool call and hands the text back to the agent. secret-scan refuses a
push by exiting 1, as git expects of a pre-push hook. Everything else exits 0.

Zero dependencies: Python 3.9+ standard library only.
"""

__version__ = "1.1.0"

import ast
import fnmatch
import glob
import hashlib
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterator, List, NoReturn, Optional, Tuple



def kit_root() -> str:
    """A kit checkout keeps this module in tools/ beside guardrails.json. Installed as a
    package it lives in site-packages, so the kit is the project the hook runs for."""
    tools = os.path.dirname(os.path.realpath(__file__))
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
    """One row in .guardrails/decisions.jsonl. Logging never changes a verdict, so
    any error writing the row is ignored."""
    try:
        tool_input = RUN["payload"].get("tool_input") or {}
        subject = (tool_input.get("command") or tool_input.get("file_path")
                   or tool_input.get("notebook_path") or "")
        row = {"ts": int(time.time()), "guard": RUN["guard"], "decision": decision,
               "rule": rule, "subject": " ".join(str(subject).split())[:300]}
        with open(os.path.join(state_dir(), "decisions.jsonl"), "a") as log:
            log.write(json.dumps(row) + "\n")
    except Exception:
        pass


def sample_allow_every() -> int:
    try:
        with open(config_path()) as handle:
            section = json.load(handle).get("decision_log") or {}
        return int(section.get("sample_allow_every", DEFAULT_SAMPLE_ALLOW_EVERY))
    except Exception:
        return DEFAULT_SAMPLE_ALLOW_EVERY


def target_path(payload: Dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return os.path.realpath(os.path.expanduser(raw)) if raw else ""


def matched_pattern(path: str, patterns: List[str], root: str) -> Optional[str]:
    """fnmatch globs, absolute or relative to the project root. `*` crosses `/`."""
    relative = os.path.relpath(path, root)
    for pattern in patterns:
        absolute = pattern if os.path.isabs(pattern) else os.path.join(root, pattern)
        if fnmatch.fnmatch(path, os.path.expanduser(absolute)) or fnmatch.fnmatch(relative, pattern):
            return pattern
    return None


# ---------------------------------------------------------------- reading a shell command

# A heredoc body is data for the command that reads it, not more commands: a body
# line such as `> rules/x.md` must not read as a redirect. `<<<` is a here-string.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
REDIRECT = re.compile(r"[0-9]*(?:&>>|&>|>>|>\||>&|>|<<<|<<-|<<|<&|<>|<)")
OPERATORS = ("&&", "||", "|&", ";;", ";", "|", "&", "(", ")", "`", "\n")
HOME = os.path.expanduser("~")

Command = Tuple[List[str], List[Tuple[str, str]]]


def strip_heredoc_bodies(command: str) -> str:
    kept, pending = [], []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(match.group(2) for match in HEREDOC.finditer(line))
    return "\n".join(kept)


def shell_tokens(command: str) -> List[Tuple[str, str]]:
    """Split a command into ("word" | "op" | "redirect", text) the way the shell
    would: quotes and backslashes removed from words, operators and redirects kept
    apart even when written without spaces (`echo x>f`). Raises ValueError on an
    unclosed quote. `$(` opens a group like `(`, so a substitution's inner command
    is read as a command of its own."""
    tokens: List[Tuple[str, str]] = []
    word: List[str] = []
    in_word = False

    def flush() -> None:
        nonlocal word, in_word
        if in_word:
            tokens.append(("word", "".join(word)))
        word, in_word = [], False

    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c in " \t\r":
            flush()
            i += 1
        elif c == "\\":
            if command.startswith("\\\n", i):
                i += 2
                continue
            word.append(command[i + 1:i + 2])
            in_word = True
            i += 2
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise ValueError("unclosed single quote")
            word.append(command[i + 1:end])
            in_word = True
            i = end + 1
        elif c == '"':
            i += 1
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"\\$`':
                    i += 1
                word.append(command[i])
                i += 1
            if i >= n:
                raise ValueError("unclosed double quote")
            in_word = True
            i += 1
        elif c == "#" and not in_word:
            end = command.find("\n", i)
            i = n if end < 0 else end
        elif c == "$" and command.startswith("$(", i):
            flush()
            tokens.append(("op", "("))
            i += 2
        else:
            match = REDIRECT.match(command, i) if (not in_word or c in "<>") else None
            if match:
                flush()
                tokens.append(("redirect", match.group()))
                i = match.end()
                continue
            op = next((op for op in OPERATORS if command.startswith(op, i)), None)
            if op:
                flush()
                tokens.append(("op", op))
                i += len(op)
                continue
            word.append(c)
            in_word = True
            i += 1
    flush()
    return tokens


ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Words that run the command after them. Each maps to its options that take a
# value, so that value is not read as the command.
WRAPPERS = {
    "command": set(), "builtin": set(), "nohup": set(), "time": set(),
    "exec": {"-a"}, "nice": {"-n"}, "env": {"-u", "-C", "-S", "-P"},
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-T", "-r", "-t"},
}


def normalized_tokens(tokens: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """The tokens with every command word in one spelling: wrappers (env, command,
    sudo...) and leading VAR=value words dropped, the directory stripped (/bin/rm),
    the name lowercased (a case-insensitive filesystem runs RM as rm), and an alias
    defined earlier in the same command replaced by its value. Arguments are left
    exactly as written."""
    out: List[Tuple[str, str]] = []
    queue = list(tokens)
    aliases: Dict[str, str] = {}
    at_start, wrapper, head, expansions = True, None, "", 0
    while queue:
        kind, text = queue.pop(0)
        if kind != "word":
            out.append((kind, text))
            if kind == "op":
                at_start, wrapper, head = True, None, ""
            elif queue and queue[0][0] == "word":
                out.append(queue.pop(0))  # a redirect's target is never the command word
            continue
        if not at_start:
            if head == "alias" and "=" in text:
                name, value = text.split("=", 1)
                aliases[name.lower()] = value
            out.append((kind, text))
            continue
        if wrapper is not None and text.startswith("-"):
            if text in WRAPPERS[wrapper] and queue and queue[0][0] == "word":
                queue.pop(0)
            continue
        if ASSIGNMENT.match(text):
            continue
        name = os.path.basename(text).lower()
        if name in WRAPPERS:
            wrapper = name
            continue
        if name in aliases and expansions < 20:  # bounded: an alias may name itself
            expansions += 1
            queue[0:0] = shell_tokens(aliases[name])
            continue
        out.append((kind, name))
        at_start, wrapper, head = False, None, name
    return out


def command_tokens(command: str) -> List[Tuple[str, str]]:
    return normalized_tokens(shell_tokens(strip_heredoc_bodies(command)))


def normalized_command(command: str) -> Optional[str]:
    """The command rendered back to one line from its normalized tokens, so the
    configured rule patterns can be matched against it as well as the raw text.
    None when the command does not parse."""
    try:
        tokens = command_tokens(command)
    except ValueError:
        return None
    parts = []
    for kind, text in tokens:
        if kind == "word":
            parts.append(shlex.quote(text))
        else:
            parts.append(";" if text == "\n" else text)
    return " ".join(parts)


def simple_commands(command: str) -> List[Command]:
    """-> [(words, [(redirect operator, its target)])], one per simple command,
    with each command word in its normalized spelling."""
    commands: List[Command] = []
    words: List[str] = []
    redirects: List[Tuple[str, str]] = []
    pending = None
    for kind, text in command_tokens(command):
        if kind == "op":
            if words or redirects:
                commands.append((words, redirects))
            words, redirects, pending = [], [], None
        elif kind == "redirect":
            pending = text
        elif pending is not None:
            redirects.append((pending, text))
            pending = None
        else:
            words.append(text)
    if words or redirects:
        commands.append((words, redirects))
    return commands


def resolve_word(word: str, cwd: Optional[str]) -> Optional[str]:
    """A shell word as a real absolute path, or None when it depends on something
    this guard cannot see: a variable other than $HOME or $TMPDIR, or a relative
    path in a folder it does not know."""
    path = re.sub(r"^~(?=/|$)", lambda _: HOME, word)
    path = re.sub(r"\$\{HOME\}|\$HOME\b", lambda _: HOME, path)
    if os.environ.get("TMPDIR"):
        path = re.sub(r"\$\{TMPDIR\}|\$TMPDIR\b", lambda _: os.environ["TMPDIR"], path)
    if "$" in path or not path:
        return None
    if not os.path.isabs(path):
        if cwd is None:
            return None
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def operands(args: List[str]) -> List[str]:
    return [arg for arg in args if not arg.startswith("-") or arg == "-"]


def copy_destinations(args: List[str], cwd: Optional[str]) -> List[str]:
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
    resolved = resolve_word(destination, cwd)
    if destination.endswith("/") or (resolved and os.path.isdir(resolved)):
        return [os.path.join(destination, os.path.basename(s.rstrip("/"))) for s in sources]
    return [destination]


def commands_in_folder(command: str, cwd: Optional[str]) -> Iterator[Tuple[str, List[str], List[Tuple[str, str]], Optional[str]]]:
    """-> (name, args, redirects, folder it runs in) per simple command. A `cd`
    moves the folder for the commands after it; one the guard cannot read leaves
    the folder unknown (None), so relative paths after it stay unresolved."""
    for words, redirects in simple_commands(command):
        name, args = (words[0], words[1:]) if words else ("", [])
        yield name, args, redirects, cwd
        if name == "cd":
            rest = operands(args)
            cwd = resolve_word(rest[0], cwd) if rest else HOME


def written_paths(command: str, cwd: Optional[str]) -> List[str]:
    """Every file this command writes through a redirect, tee, sed -i, cp or mv,
    resolved to a real path."""
    found = []
    for name, args, redirects, folder in commands_in_folder(command, cwd):
        targets = [target for op, target in redirects
                   if ">" in op and not (op.endswith("&") and re.fullmatch(r"[0-9]+|-", target))]
        if name == "tee":
            targets += operands(args)
        elif name == "sed" and any(re.match(r"^-[a-zA-Z]*i|^--in-place", arg) for arg in args):
            targets += operands(args)
        elif name in ("cp", "mv"):
            targets += copy_destinations(args, folder)
        for target in targets:
            path = resolve_word(target, folder)
            if path and path != "/dev/null":
                found.append(path)
    return found


def temp_roots() -> List[str]:
    roots = {tempfile.gettempdir(), "/tmp", "/var/tmp", os.environ.get("TMPDIR") or "/tmp"}
    return sorted({os.path.realpath(root) for root in roots})


def deletes_only_temp(command: str, cwd: Optional[str]) -> bool:
    """True when the command has at least one recursive rm and every path each one
    names resolves strictly inside a temp folder. A path that cannot be resolved,
    the temp folder itself, a `..` or a symlink that leads out all count as outside;
    so does a glob with any match outside."""
    roots = temp_roots()

    def inside(path: str) -> bool:
        return any(path.startswith(root + os.sep) for root in roots)

    found = False
    for name, args, _, folder in commands_in_folder(command, cwd):
        if name != "rm":
            continue
        if "--" in args:
            split = args.index("--")
            options, targets = args[:split], args[split + 1:]
        else:
            options, targets = args, operands(args)
        if not any(re.match(r"^-[a-zA-Z]*[rR]|^--recursive$", arg) for arg in options):
            continue
        found = True
        for target in targets:
            path = resolve_word(target, folder)
            if not path or not inside(path):
                return False
            if any(not inside(os.path.realpath(match)) for match in glob.glob(path)):
                return False
    return found


# ---------------------------------------------------------------- command guard

def shell_write_check(command: str, flat: str, cwd: Optional[str]) -> None:
    """The file lock sees the Write and Edit tools only, so the same protected paths
    are refused here when a shell command would write them."""
    protected = guard_config("file_lock").get("protected") or []
    if not protected:
        return
    try:
        paths = written_paths(command, cwd)
    except ValueError:
        return  # a command the shell itself would reject; the rules above still ran
    root = project_dir()
    for path in paths:
        pattern = matched_pattern(path, protected, root)
        if pattern:
            deny(
                "BLOCKED by command-guard [shell-write-protected]: this command writes %s, a\n"
                "  protected file (it matches `%s` in %s).\n"
                "  command: %s\n"
                "  instead: make the change with the Write or Edit tool, where the file lock asks\n"
                "  for an approval token; a shell write would go around that check."
                % (path, pattern, config_path(), flat), "shell-write-protected")


def safe_temp_delete(command: str, cwd: Optional[str]) -> bool:
    try:
        return deletes_only_temp(command, cwd)
    except ValueError:
        return False


def command_guard() -> None:
    payload = hook_input()
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        sys.exit(0)
    flat = " ".join(command.split())
    cwd = payload.get("cwd")
    cwd = os.path.realpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None
    config = guard_config("command_guard")
    allowlist = config.get("allowlist") or []
    # Each rule is matched against the command as written and in its normalized
    # spelling; the allowlist reads only the command as written.
    spellings = [flat] + [text for text in [normalized_command(command)] if text]

    for rule in config.get("rules") or []:
        if not any(re.search(rule["pattern"], text) for text in spellings):
            continue
        if rule.get("allow_in_temp") and safe_temp_delete(command, cwd):
            continue
        if any(entry.get("rule") == rule["id"] and re.fullmatch(entry.get("command", r"(?!)"), flat)
               for entry in allowlist):
            continue
        deny(
            "BLOCKED by command-guard [%s]: %s\n"
            "  command: %s\n"
            "  instead: %s\n"
            "  If this exact command is genuinely safe here, allowlist it in %s under\n"
            "  command_guard.allowlist: {\"rule\": \"%s\", \"command\": \"^...$\", \"why\": \"...\"}.\n"
            "  The pattern must match the whole command, so an allowlist entry frees one\n"
            "  command, never a shape."
            % (rule["id"], rule["blocks"], flat, rule["instead"], config_path(), rule["id"]),
            rule["id"])
    shell_write_check(command, flat, cwd)
    sys.exit(0)


# ---------------------------------------------------------------- protected-file lock

def token_file() -> str:
    return os.path.join(state_dir(), "lock-approval.token")


def read_token(path: str) -> Optional[Tuple[str, int, List[str]]]:
    """-> (batch, expires_epoch, [covered paths]) or None. A token with no file list
    covers nothing: an unscoped token is the hole this lock exists to close."""
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    batch, expires, covered, in_files = "", 0, [], False
    for line in lines:
        if in_files:
            if line.strip():
                covered.append(line.strip())
        elif line.startswith("batch: "):
            batch = line[7:]
        elif line.startswith("expires: "):
            expires = int(line[9:] or 0)
        elif line.strip() == "files:":
            in_files = True
    return (batch, expires, covered)


def token_covers(path: str, covered: List[str]) -> bool:
    for entry in covered:
        if entry.endswith("/") and path.startswith(entry):
            return True
        if entry == path:
            return True
    return False


def mint_hint(path: str) -> str:
    return '%s "<batch label>" "%s"' % (invocation("lock-approve"), path)


def file_lock() -> None:
    path = target_path(hook_input())
    if not path:
        sys.exit(0)
    config = guard_config("file_lock")
    pattern = matched_pattern(path, config.get("protected") or [], project_dir())
    if not pattern:
        sys.exit(0)

    minutes = int(config.get("token_ttl_seconds", 1800)) // 60
    head = ("BLOCKED by file-lock-guard: %s is protected (it matches `%s` in %s).\n"
            % (path, pattern, config_path()))
    token = read_token(token_file())
    if token is None:
        deny(head +
             "  There is no approval token. Once a human has approved this change, mint one:\n"
             "    %s\n"
             "  A token names the files it covers and expires after %d minutes."
             % (mint_hint(path), minutes), "no-token")
    batch, expires, covered = token
    if time.time() >= expires:
        deny(head + '  The token for batch "%s" expired %d minutes ago. Mint a fresh one:\n    %s'
             % (batch, int((time.time() - expires) // 60), mint_hint(path)), "expired-token")
    if not token_covers(path, covered):
        deny(head + '  The open token for batch "%s" does not cover this file. It covers:\n%s\n'
             "  Approval is per batch, so a live token from other work is not a yes for this\n"
             "  one. Mint a token that names this file:\n    %s"
             % (batch, "\n".join("    " + item for item in covered) or "    (nothing)",
                mint_hint(path)), "token-does-not-cover")
    sys.exit(0)


def lock_approve(argv: List[str]) -> None:
    if len(argv) < 2:
        die('usage: %s "<batch label>" <path> [more paths...]\n'
            % invocation("lock-approve") +
            "        a token must name every file it approves — no files, no token")
    label, paths = argv[0], argv[1:]
    ttl = int(cli_config("file_lock").get("token_ttl_seconds", 1800))
    expires = int(time.time()) + ttl

    covered = []
    for raw in paths:
        resolved = os.path.realpath(os.path.expanduser(raw))
        covered.append(resolved + "/" if os.path.isdir(resolved) else resolved)

    path = token_file()
    with open(path, "w") as handle:
        handle.write("batch: %s\nexpires: %d\nfiles:\n%s\n"
                     % (label, expires, "\n".join(covered)))
    os.chmod(path, 0o600)
    with open(os.path.join(state_dir(), "lock-approvals.jsonl"), "a") as log:
        log.write(json.dumps({"ts": int(time.time()), "batch": label, "files": covered}) + "\n")

    print('Approval token minted for batch "%s" (%d minutes).' % (label, ttl // 60))
    for item in covered:
        print("  covers: %s" % item)


# ---------------------------------------------------------------- syntax guard

# `python3 -c '` followed by its body, with any flags (and one flag value) between.
PYTHON_C = re.compile(r"python3?\s+(?:-[A-Za-z]+(?:\s+[^-\s'\"]\S*)?\s+)*-c\s+'")


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


def heredoc_bodies(script: str) -> List[Tuple[int, int, str, Any]]:
    """-> [(body start, body end, the opener line up to its <<, the opener match)],
    read line by line as the shell does, so an opener inside a body is body text."""
    bodies = []
    pending: List[Tuple[Any, str]] = []
    start, offset = None, 0
    for line in script.split("\n"):
        if pending:
            match, opener = pending[0]
            if start is None:
                start = offset
            if line.strip() == match.group(2):
                bodies.append((start, offset, opener[:match.start()], match))
                pending.pop(0)
                start = None
        else:
            pending = [(match, line) for match in HEREDOC.finditer(line)]
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
        if not match.group(1) or not re.search(r"\bpython3?\b", opener) or commented_out(start - 1):
            continue
        body = script[start:stop]
        if match.group().startswith("<<-"):
            body = re.sub(r"(?m)^\t+", "", body)
        error = embedded_python_error(body)
        if error:
            return ("the Python in heredoc %s opening at line %d does not compile (python line %s: %s)"
                    % (match.group(2), line_of(start - 1), error.lineno, error.msg))
    return None


def script_kind(path: str, text: str) -> Optional[str]:
    """"sh", "py", or None for a file this guard does not parse."""
    extension = os.path.splitext(path)[1]
    if extension in (".sh", ".bash"):
        return "sh"
    if extension == ".py":
        return "py"
    first = text.split("\n", 1)[0]
    if first.startswith("#!"):
        if "python" in first:
            return "py"
        if re.search(r"\b(ba)?sh\b", first):
            return "sh"
    return None


def syntax_problem(path: str, text: str, kind: str) -> Optional[str]:
    if kind == "py":
        error = python_error(text)
        return "python line %s: %s" % (error.lineno, error.msg) if error else None
    handle, scratch = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(text)
        result = subprocess.run(["bash", "-n", scratch], capture_output=True, text=True, timeout=10)
    finally:
        os.remove(scratch)
    if result.returncode != 0:
        lines = result.stderr.replace(scratch, path).strip().splitlines()
        return "bash -n: " + " ".join(lines[-3:])
    return embedded_python_problem(text)


def file_after(payload: Dict[str, Any], path: str) -> Optional[str]:
    """The text the file would hold once this Write or Edit lands, or None when the
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


# ---------------------------------------------------------------- pre-push secret scan

# Credential shapes with a fixed vendor prefix or frame, after the public gitleaks
# rule set. A prefix makes a hit nearly certain, which is what lets a push be
# refused outright.
SECRET_SHAPES = [
    ("aws-access-key", re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20}\b")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{80,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}|\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("private-key", re.compile(r"-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")),
]
# A name that says secret, assigned a literal of 16 or more key characters. The
# value must mix letters and digits, which keeps placeholders such as
# "your-api-key-here" and references such as ${DB_PASSWORD} out of it.
GENERIC_SECRET = re.compile(
    r"(?i)[a-z0-9_.-]*(?:api[_-]?key|secret|token|passw(?:or)?d|access[_-]?key)[a-z0-9_.-]*"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_+/=.-]{16,})")
ZERO_SHA = re.compile(r"^0+$")


def secret_shape(line: str) -> Optional[str]:
    for rule, shape in SECRET_SHAPES:
        if shape.search(line):
            return rule
    for match in GENERIC_SECRET.finditer(line):
        value = match.group(1)
        if re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value):
            return "generic-secret"
    return None


def git(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotePath=false"] + args,
                          capture_output=True, text=True, errors="replace")


def added_lines(revisions: List[str]) -> Iterator[Tuple[str, str, str]]:
    """-> (commit, path, line) for every line each commit in the range adds. Each
    commit is read on its own, so a key added and removed again before the push is
    still found: it is in the history the push publishes."""
    result = git(["log", "-p", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                  "--src-prefix=a/", "--dst-prefix=b/", "--diff-filter=ACMR",
                  "--format=commit %H"] + revisions)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git log failed")
    commit, path, in_header = "", "", False
    for line in result.stdout.split("\n"):
        if line.startswith("commit "):
            commit, path, in_header = line[7:], "", False
        elif line.startswith("diff --git "):
            path, in_header = "", True
        elif in_header:
            if line.startswith("+++ b/"):
                path = line[6:]
            elif line.startswith("@@"):
                in_header = False
        elif path and line.startswith("+"):
            yield commit, path, line[1:]


def allowed_paths(top: str) -> List[str]:
    try:
        with open(os.path.join(top, ".secret-scan-allow")) as handle:
            return [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    except OSError:
        return []


def secret_scan(argv: List[str]) -> int:
    """git pre-push hook: git passes "<local ref> <local sha> <remote ref> <remote sha>"
    on stdin, one line per ref being pushed. Exit 1 refuses the whole push."""
    top = git(["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        sys.stderr.write("%s: not inside a git repository\n" % PROG)
        return DENY
    allow = allowed_paths(top.stdout.strip())
    findings = []
    for line in sys.stdin.read().splitlines():
        fields = line.split()
        if len(fields) != 4 or ZERO_SHA.match(fields[1]):
            continue  # a deleted ref publishes nothing new
        local_sha, remote_sha = fields[1], fields[3]
        known = not ZERO_SHA.match(remote_sha) and git(["cat-file", "-e", remote_sha]).returncode == 0
        # A new branch, or a remote tip this clone has never seen: scan every commit
        # no remote-tracking branch already holds.
        revisions = ["%s..%s" % (remote_sha, local_sha)] if known else [local_sha, "--not", "--remotes"]
        try:
            for commit, path, text in added_lines(revisions):
                rule = secret_shape(text)
                if rule and not any(fnmatch.fnmatch(path, pattern) for pattern in allow):
                    findings.append((fields[2], commit[:10], path, rule))
        except RuntimeError as error:
            sys.stderr.write("%s: cannot read the pushed commits: %s\n" % (PROG, error))
            return DENY
    if not findings:
        return 0
    sys.stderr.write("secret-scan: this push adds credential-shaped text. The values are not printed.\n")
    for ref, commit, path, rule in sorted(set(findings)):
        sys.stderr.write("  %s  %s  (%s, commit %s)\n" % (ref, path, rule, commit))
    sys.stderr.write(
        "  A real key: take it out of every commit in this push (git rebase -i, or git commit\n"
        "  --amend for the last one), keep it in an environment variable or a secrets manager,\n"
        "  and rotate it if a copy has ever left this machine.\n"
        "  A fake key in a test fixture: list the path in .secret-scan-allow at the top of the\n"
        "  repository, one glob per line.\n")
    return 1


# ---------------------------------------------------------------- cross-session claims

def claims_file() -> str:
    return os.path.join(state_dir(), "claims.tsv")


def read_claims(ttl: int) -> List[List[str]]:
    now = time.time()
    rows = []
    try:
        with open(claims_file()) as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3 and now - float(parts[0]) < ttl:
                    rows.append(parts)
    except (OSError, ValueError):
        pass
    return rows


def write_claims(rows: List[List[str]]) -> None:
    path = claims_file()
    temporary = "%s.tmp.%d" % (path, os.getpid())
    with open(temporary, "w") as handle:
        handle.write("".join("\t".join(row) + "\n" for row in rows[-500:]))
    os.replace(temporary, path)


def takeover_marker(path: str) -> str:
    return os.path.join(state_dir(), "takeover-" + hashlib.sha1(path.encode()).hexdigest()[:16])


def session_id(payload: Dict[str, Any]) -> str:
    return os.environ.get("GUARDRAILS_SESSION_ID") or payload.get("session_id") or "unknown"


def claims_guard() -> None:
    payload = hook_input()
    path = target_path(payload)
    root = project_dir()
    if not path or not path.startswith(root + os.sep):
        sys.exit(0)
    config = guard_config("claims")
    if matched_pattern(path, config.get("exempt") or [], root):
        sys.exit(0)

    ttl = int(config.get("ttl_seconds", 1800))
    mine = session_id(payload)
    rows = read_claims(ttl)
    holders = [row for row in rows if row[2] == path and row[1] != mine]

    marker = takeover_marker(path)
    granted = (os.path.exists(marker)
               and time.time() - os.path.getmtime(marker) < int(config.get("takeover_ttl_seconds", 600)))
    if holders and not granted:
        held_at, holder, _ = holders[-1]
        deny(
            "BLOCKED by claims-guard: another session is already writing %s.\n"
            "  holder: %s (claimed %d minutes ago)\n"
            "  Two sessions editing one file means the second write silently eats the first.\n"
            "  If that session is finished, release the claim:\n"
            '    %s --session "%s" "%s"\n'
            "  If your work outranks theirs, take it over — logged, and what they were holding\n"
            "  is written to the takeover ledger so it can be picked up rather than lost:\n"
            '    %s "%s" "<why yours wins>"'
            % (path, holder, int((time.time() - float(held_at)) // 60),
               invocation("claims-clear"), holder, path,
               invocation("claims-takeover"), path), "claimed-by-another-session")

    rows = [row for row in rows if not (row[1] == mine and row[2] == path)]
    rows.append(["%.0f" % time.time(), mine, path])
    write_claims(rows)
    sys.exit(0)


def claims_clear(argv: List[str]) -> None:
    who = os.environ.get("GUARDRAILS_SESSION_ID") or ""
    paths = []
    index = 0
    while index < len(argv):
        if argv[index] == "--session" and index + 1 < len(argv):
            who, index = argv[index + 1], index + 2
            continue
        paths.append(os.path.realpath(os.path.expanduser(argv[index])))
        index += 1

    if not who and not sys.stdin.isatty():
        # SessionEnd wiring: the session being cleared is the one in the hook payload.
        try:
            who = json.loads(sys.stdin.read() or "{}").get("session_id") or ""
        except Exception:
            who = ""
    if not who:
        die('usage: %s [--session <id>] [path...]\n' % invocation("claims-clear") +
            "        with no --session, the id comes from GUARDRAILS_SESSION_ID or a hook payload")

    config = cli_config("claims")
    rows = read_claims(int(config.get("ttl_seconds", 1800)))
    kept = [row for row in rows
            if not (row[1] == who and (not paths or row[2] in paths))]
    write_claims(kept)
    print("Released %d claim(s) held by %s." % (len(rows) - len(kept), who))


def claims_takeover(argv: List[str]) -> None:
    if len(argv) < 2:
        die('usage: %s <path> "<one-line reason>"\n' % invocation("claims-takeover") +
            "        the reason is the ledger entry a human reads later")
    path = os.path.realpath(os.path.expanduser(argv[0]))
    reason = argv[1]
    config = cli_config("claims")
    ttl = int(config.get("takeover_ttl_seconds", 600))

    rows = read_claims(int(config.get("ttl_seconds", 1800)))
    holders = [row for row in rows if row[2] == path]
    displaced = holders[-1] if holders else None

    with open(takeover_marker(path), "w") as handle:
        handle.write(reason + "\n")
    entry = {
        "ts": int(time.time()),
        "file": path,
        "reason": reason,
        "taken_by": os.environ.get("GUARDRAILS_SESSION_ID") or "cli",
        "displaced_session": displaced[1] if displaced else None,
        "displaced_claim_age_seconds": int(time.time() - float(displaced[0])) if displaced else None,
    }
    with open(os.path.join(state_dir(), "takeover-ledger.jsonl"), "a") as log:
        log.write(json.dumps(entry) + "\n")

    print("Takeover granted for %d minutes, this file only: %s" % (ttl // 60, path))
    print("Ledgered in .guardrails/takeover-ledger.jsonl — displaced session: %s"
          % (entry["displaced_session"] or "none"))
    print("Whatever that session had pending is now yours to carry, not to drop.")


# ---------------------------------------------------------------- gate liveness

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


# ---------------------------------------------------------------- dispatch

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


if __name__ == "__main__":
    main()

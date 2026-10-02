"""Reading a shell command the way the shell would: words, operators, redirects,
the command word in one spelling, and the folder each command runs in."""

import os
import re
import shlex
from typing import Dict, FrozenSet, Iterator, List, Optional, Tuple


# A heredoc body is data for the command that reads it, not more commands: a body
# line such as `> rules/x.md` must not read as a redirect. `<<<` is a here-string.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
REDIRECT = re.compile(r"[0-9]*(?:&>>|&>|>>|>\||>&|>|<<<|<<-|<<|<&|<>|<)")
OPERATORS = ("&&", "||", "|&", ";;", ";", "|", "&", "(", ")", "`", "\n")
HOME = os.path.expanduser("~")

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


def reassigned_names(command: str) -> FrozenSet[str]:
    """$HOME and $TMPDIR are read from the guard's own environment, which is only
    right when the command does not set them itself (`TMPDIR=$HOME; rm -rf ...`)."""
    bare = re.sub(r"\$\{?(?:HOME|TMPDIR)\}?", "", command)
    return frozenset(name for name in ("HOME", "TMPDIR") if re.search(r"\b%s\b" % name, bare))


def resolve_word(word: str, cwd: Optional[str], unreadable: FrozenSet[str] = frozenset()) -> Optional[str]:
    """A shell word as a real absolute path, or None when it depends on something
    this guard cannot see: a variable other than $HOME or $TMPDIR (or one of those
    the command sets itself), a brace list, ~user, or a relative path in a folder
    it does not know."""
    known = {"HOME": HOME, "TMPDIR": os.environ.get("TMPDIR")}
    path = word
    if re.match(r"~(/|$)", path) and "HOME" not in unreadable:
        path = HOME + path[1:]
    for name, value in known.items():
        if value and name not in unreadable:
            path = re.sub(r"\$\{%s\}|\$%s\b" % (name, name), lambda _: value, path)
    if not path or path.startswith("~") or re.search(r"[$`{}]", path):
        return None
    if not os.path.isabs(path):
        if cwd is None:
            return None
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def operands(args: List[str]) -> List[str]:
    return [arg for arg in args if not arg.startswith("-") or arg == "-"]


def folder_after(name: str, args: List[str], cwd: Optional[str], unreadable: FrozenSet[str]) -> Optional[str]:
    if name in ("pushd", "popd"):
        return None
    if name != "cd":
        return cwd
    rest = operands(args)
    if not rest:
        return None if "HOME" in unreadable else HOME
    folder = resolve_word(rest[0], cwd, unreadable)
    return folder if folder and os.path.isdir(folder) else None  # a cd that fails leaves us guessing


def commands_in_folder(command: str, cwd: Optional[str]) -> Iterator[Tuple[str, List[str], List[Tuple[str, str]], Optional[str]]]:
    """-> (name, args, [(redirect operator, its target)], folder it runs in) per
    simple command, with the command word in its normalized spelling. A `cd` into
    an existing folder moves the folder for the commands after it, until the end of
    the subshell it ran in. Any other cd, and pushd or popd, leaves the folder
    unknown (None), so relative paths after it stay unresolved."""
    unreadable = reassigned_names(command)
    words: List[str] = []
    redirects: List[Tuple[str, str]] = []
    pending = None
    outer: List[Optional[str]] = []  # the folder outside each open subshell
    in_backtick = False
    for kind, text in command_tokens(command) + [("op", "\n")]:
        if kind == "redirect":
            pending = text
            continue
        if kind == "word":
            if pending is not None:
                redirects.append((pending, text))
                pending = None
            else:
                words.append(text)
            continue
        if words or redirects:
            name, args = (words[0], words[1:]) if words else ("", [])
            yield name, args, redirects, cwd
            cwd = folder_after(name, args, cwd, unreadable)
        words, redirects, pending = [], [], None
        opens = text == "(" or (text == "`" and not in_backtick)
        closes = text == ")" or (text == "`" and in_backtick)
        if text == "`":
            in_backtick = not in_backtick
        if opens:
            outer.append(cwd)
        elif closes and outer:
            cwd = outer.pop()

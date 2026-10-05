"""Reading a shell command the way the shell would: words, operators, redirects,
the command word in one spelling, and the folder each command runs in."""

import glob
import os
import re
import shlex
from typing import Dict, FrozenSet, Iterator, List, NamedTuple, Optional, Tuple

from .git_rules import canonical_git_args


# A heredoc body is data for the command that reads it, not more commands: a body
# line such as `> rules/x.md` must not read as a redirect. `<<<` is a here-string.
# This finds the operator; heredoc_word reads the delimiter after it.
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?")
REDIRECT = re.compile(r"[0-9]*(?:&>>|&>|>>|>\||>&|>|<<<|<<-|<<|<&|<>|<)")
OPERATORS = ("&&", "||", "|&", ";;", ";", "|", "&", "(", ")", "`", "\n")
HOME = os.path.expanduser("~")

class Heredoc(NamedTuple):
    start: int         # where its << stands on the opener line
    delimiter: str     # the end line, quotes and backslashes removed
    quoted: bool       # any quote or backslash in the word: the body is not expanded
    strip_tabs: bool   # <<- : leading tabs are dropped from the body and the end line


def heredoc_word(line: str, i: int) -> Tuple[str, bool, int]:
    """The delimiter word starting at `i` (after any blanks) as the shell reads it:
    every character up to an unquoted blank or operator, with quotes and
    backslashes removed. -> (delimiter, quoted, index after the word)."""
    out: List[str] = []
    quoted, n = False, len(line)
    while i < n and line[i] in " \t":
        i += 1
    while i < n and line[i] not in " \t;&|<>()":
        c = line[i]
        if c == "\\" and i + 1 < n:
            out.append(line[i + 1])
            quoted, i = True, i + 2
        elif c in "'\"":
            end = line.find(c, i + 1)
            end = n if end < 0 else end
            body = line[i + 1:end]
            out.append(re.sub(r'\\([$`"\\])', r"\1", body) if c == '"' else body)
            quoted, i = True, end + 1
        else:
            out.append(c)
            i += 1
    return "".join(out), quoted, i


def ends_heredoc(line: str, opener: Heredoc) -> bool:
    """The shell ends a body only at a line exactly equal to the delimiter."""
    return (line.lstrip("\t") if opener.strip_tabs else line) == opener.delimiter


def heredoc_openers(line: str) -> List[Heredoc]:
    """The heredoc openers on one line. A << inside quotes or a comment is text: read
    as an opener, it would swallow the real commands after it as a body."""
    openers: List[Heredoc] = []
    quote, i = "", 0
    starts = {match.start(): match for match in HEREDOC.finditer(line)}
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|("):
            break
        elif i in starts:
            operator = starts[i]
            delimiter, quoted, i = heredoc_word(line, operator.end())
            if delimiter:
                openers.append(Heredoc(operator.start(), delimiter, quoted, operator.group() == "<<-"))
            continue
        i += 1
    return openers


def strip_heredoc_bodies(command: str) -> str:
    """The command without its heredoc bodies. A body that never ends runs to the
    end of the command, as in the shell; its opener line is kept either way."""
    kept: List[str] = []
    pending: List[Heredoc] = []
    for line in command.split("\n"):
        if pending:
            if ends_heredoc(line, pending[0]):
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(heredoc_openers(line))
    return "\n".join(kept)


ANSI_C_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b",
                  "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def ansi_c_word(command: str, start: int) -> Tuple[str, int]:
    """The text of the $'...' word whose $ is at `start`, and the index after it.
    Unlike '...', it takes backslash escapes, so \\' does not end it."""
    out, i, n = [], start + 2, len(command)
    while i < n:
        c = command[i]
        if c == "'":
            return "".join(out), i + 1
        if c == "\\" and i + 1 < n:
            nxt = command[i + 1]
            numeric = re.match(r"x([0-9A-Fa-f]{1,2})|([0-7]{1,3})", command[i + 1:])
            if nxt in ANSI_C_ESCAPES:
                out.append(ANSI_C_ESCAPES[nxt])
                i += 2
            elif numeric:
                hex_digits, octal = numeric.groups()
                out.append(chr(int(hex_digits, 16) if hex_digits else int(octal, 8)))
                i += 1 + len(numeric.group())
            else:
                out.append(c + nxt)
                i += 2
            continue
        out.append(c)
        i += 1
    raise ValueError("unclosed $' quote")


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
        elif c == "$" and command.startswith("$'", i):
            text, i = ansi_c_word(command, i)
            word.append(text)
            in_word = True
        elif c == "#" and not in_word:
            end = command.find("\n", i)
            i = n if end < 0 else end
        elif c == "$" and re.match(r"\$(IFS\b|\{IFS\})", command[i:]):
            flush()  # unquoted $IFS expands to a separator: rm${IFS}-rf is two words
            i += 4 if command.startswith("$IFS", i) else 6
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
# The shell's reserved words. In command position each one opens or closes a
# group, a branch or a loop, or negates what follows, and the next word is the
# command: read as the command name, `{` would hide the rm in `{ rm -r x -f; }`.
RESERVED_WORDS = {"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until"}


def normalized_tokens(tokens: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """The tokens with every command word in one spelling: wrappers (env, command,
    sudo...) and leading VAR=value words dropped, the directory stripped (/bin/rm),
    the name lowercased (a case-insensitive filesystem runs RM as rm), and an alias
    defined earlier in the same command replaced by its value. A reserved word in
    command position ({, if, do, !...) becomes a separator. Arguments are left
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
        if text in RESERVED_WORDS and wrapper in (None, "time"):  # time is a reserved word too
            out.append(("op", text))  # a separator: the next word starts a command
            wrapper = None
            continue
        if re.search(r"[*?[]", text):
            # The shell globs a command word too: /bin/r[m] runs /bin/rm, and any
            # further matches become its first arguments.
            matches = sorted(glob.glob(text))
            if matches:
                text = matches[0]
                queue[0:0] = [("word", match) for match in matches[1:]]
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


RM_LONG_FLAGS = {"--recursive": "r", "--force": "f", "--dir": "d", "--verbose": "v",
                 "--interactive": "i"}
# Every long option GNU rm has: it takes any prefix that names exactly one of them.
RM_LONG_OPTIONS = sorted(RM_LONG_FLAGS) + ["--help", "--no-preserve-root", "--one-file-system",
                                           "--preserve-root", "--version"]


def rm_long_flag(arg: str) -> str:
    """The short letter for an rm long option, written in full or as any
    unambiguous prefix (--rec is --recursive; --v is --verbose or --version)."""
    name = arg.split("=", 1)[0]
    if name in RM_LONG_FLAGS:
        return RM_LONG_FLAGS[name]
    named = [option for option in RM_LONG_OPTIONS if option.startswith(name)]
    return RM_LONG_FLAGS.get(named[0], "") if len(named) == 1 else ""
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def canonical_rm_args(args: List[str]) -> List[str]:
    """rm's arguments with every flag gathered into one sorted cluster in front:
    -r --force, -rv -f, --recursive -f and --rec --forc all become -fr (-frv),
    wherever they stood. -R is -r. Words after -- are operands, as rm reads them."""
    letters, rest, operands_only = set(), [], False
    for arg in args:
        if operands_only or arg == "-" or not arg.startswith("-"):
            rest.append(arg)
        elif arg == "--":
            operands_only = True
            rest.append(arg)
        elif arg.startswith("--"):
            letters.update(rm_long_flag(arg))
        else:
            letters.update(arg[1:].replace("R", "r"))
    return (["-" + "".join(sorted(letters))] if letters else []) + rest


def nested_script(name: str, args: List[str]) -> Optional[str]:
    """The command text a shell -c or eval will run, if this is one."""
    if name == "eval":
        return " ".join(args)
    if name in SHELLS:
        for index, arg in enumerate(args):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg) and index + 1 < len(args):
                return args[index + 1]
    return None


def render_command(words: List[str], depth: int) -> str:
    name, args = words[0], words[1:]
    if name == "rm":
        args = canonical_rm_args(args)
    elif name == "git":
        args = canonical_git_args(args)
    text = " ".join(shlex.quote(word) for word in [name] + args)
    script = nested_script(name, args)
    if script and depth < 3:
        inner = normalized_command(script, depth + 1)
        if inner:
            text += " ; " + inner
    return text


def normalized_command(command: str, depth: int = 0) -> Optional[str]:
    """The command rendered back to one line from its normalized tokens, so the
    configured rule patterns can be matched against it as well as the raw text.
    rm and git get their arguments in one spelling too, and the text a shell -c or
    eval runs is appended, read the same way. None when the command does not parse."""
    try:
        tokens = command_tokens(command)
    except ValueError:
        return None
    parts: List[str] = []
    words: List[str] = []
    target_next = False
    for kind, text in tokens:
        if kind == "word" and not target_next:
            words.append(text)
            continue
        if words:
            parts.append(render_command(words, depth))
            words = []
        if kind == "word":
            parts.append(shlex.quote(text))  # a redirect's target
        else:
            parts.append(";" if text == "\n" else text)
        target_next = kind == "redirect"
    if words:
        parts.append(render_command(words, depth))
    return " ".join(parts)


def reassigned_names(command: str) -> FrozenSet[str]:
    """$HOME and $TMPDIR are read from the guard's own environment, which is only
    right when the command does not set them itself (`TMPDIR=$HOME; rm -rf ...`)."""
    bare = re.sub(r"\$\{?(?:HOME|TMPDIR)\}?", "", command)
    return frozenset(name for name in ("HOME", "TMPDIR") if re.search(r"\b%s\b" % name, bare))


# More words than this from one brace list is a command built to hide something.
BRACE_LIMIT = 1024


def brace_expansions(word: str) -> List[str]:
    """The words the shell makes of one word by brace expansion: a{b,c}d is abd and
    acd, lists nest, and an alternative that comes out empty drops out. A range
    ({1..9}, {a..z}) becomes a * glob, read as every name it could make. ${...} is a
    variable, not a list. Quotes are gone by the time a word is read here, so a
    quoted list is expanded too, which can only refuse more. Raises ValueError past
    BRACE_LIMIT words."""
    search = 0
    while True:
        start = next((i for i in range(search, len(word))
                      if word[i] == "{" and (i == 0 or word[i - 1] != "$")), None)
        if start is None:
            return [word]
        depth, commas, close = 0, [], None
        for i in range(start, len(word)):
            if word[i] == "{":
                depth += 1
            elif word[i] == "}":
                depth -= 1
                if depth == 0:
                    close = i
                    break
            elif word[i] == "," and depth == 1:
                commas.append(i)
        if close is None:  # unmatched: the shell leaves it, and reads on for a later list
            search = start + 1
            continue
        prefix, suffix = word[:start], word[close + 1:]
        if commas:
            cuts = [start] + commas + [close]
            words: List[str] = []
            for a, b in zip(cuts, cuts[1:]):
                words += brace_expansions(prefix + word[a + 1:b] + suffix)
                if len(words) > BRACE_LIMIT:
                    raise ValueError("a brace list of more than %d words" % BRACE_LIMIT)
            return [w for w in words if w]
        if ".." in word[start + 1:close]:
            return brace_expansions(prefix + "*" + suffix)
        search = start + 1  # {} or {word}: the shell leaves it as written


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


# Words after which a cd may or may not have run, or run somewhere this reading
# cannot see: a brace group or a branch hides it, eval and source run text the
# guard never parsed. Each one leaves the folder unknown. The reserved words
# arrive as separators, the rest as command names.
FOLDER_HIDING_WORDS = RESERVED_WORDS | {
    "for", "case", "esac", "select", "function", "[[", "eval", "source", ".",
    "pushd", "popd",
}


def folder_after(name: str, args: List[str], cwd: Optional[str], unreadable: FrozenSet[str]) -> Optional[str]:
    if name in FOLDER_HIDING_WORDS:
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
        if text in FOLDER_HIDING_WORDS:
            cwd = None
        opens = text == "(" or (text == "`" and not in_backtick)
        closes = text == ")" or (text == "`" and in_backtick)
        if text == "`":
            in_backtick = not in_backtick
        if opens:
            outer.append(cwd)
        elif closes and outer:
            cwd = outer.pop()

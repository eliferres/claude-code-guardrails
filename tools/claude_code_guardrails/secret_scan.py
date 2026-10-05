"""The git pre-push secret scan."""

import fnmatch
import os
import re
import subprocess
import sys
from typing import Iterator, List, Optional, Tuple

from .config import DENY, PROG


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
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9+/]{30,}")),
    ("sendgrid-key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z]{34}\b")),
    ("pypi-token", re.compile(r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}")),
    ("stripe-webhook-secret", re.compile(r"\bwhsec_[A-Za-z0-9]{32,}\b")),
]
# A name that says secret, assigned a literal of 16 or more key characters. The
# value must mix letters and digits, which keeps placeholders such as
# "your-api-key-here" and references such as ${DB_PASSWORD} out of it. An
# unquoted value that is a call or a dotted name (b64encode(raw), cfg.api_token)
# is code computing a secret, not a secret.
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
        quoted = line[match.start(1) - 1:match.start(1)] in ("'", '"')
        after = line[match.end(1):match.end(1) + 1]
        expression = after in ("(", "[") or re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", value)
        if (quoted or not expression) and re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value):
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
    """git pre-push hook: git runs it as `<hook> <remote name> <url>` and passes
    "<local ref> <local sha> <remote ref> <remote sha>" on stdin, one line per ref
    being pushed. Exit 1 refuses the whole push."""
    top = git(["rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        sys.stderr.write("%s: not inside a git repository\n" % PROG)
        return DENY
    allow = allowed_paths(top.stdout.strip())
    # Only this remote's own tracking branches say what it already has: a commit on
    # a private mirror is still new to a public remote. Pushing to a bare URL, with
    # no tracking branches at all, scans everything the pushed tip reaches.
    remote = argv[0] if argv else ""
    known_remote = remote in git(["remote"]).stdout.split()
    already_there = ["--not", "--remotes=%s" % remote] if known_remote else []
    findings = []
    for line in sys.stdin.read().splitlines():
        fields = line.split()
        if len(fields) != 4 or ZERO_SHA.match(fields[1]):
            continue  # a deleted ref publishes nothing new
        local_sha, remote_sha = fields[1], fields[3]
        known = not ZERO_SHA.match(remote_sha) and git(["cat-file", "-e", remote_sha]).returncode == 0
        # A new branch, or a remote tip this clone has never seen: scan every commit
        # this remote's tracking branches do not already hold.
        revisions = ["%s..%s" % (remote_sha, local_sha)] if known else [local_sha] + already_there
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

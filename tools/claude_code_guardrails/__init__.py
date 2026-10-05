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
push by exiting 1, as git expects of a pre-push hook. A usage or configuration
error exits 2. Everything else exits 0.

Zero dependencies: Python 3.9+ standard library only.
"""

__version__ = "1.1.0"

from .cli import main

__all__ = ["main", "__version__"]

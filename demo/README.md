# Demo workspace

A fictional agent project with the guards installed. It exists so the walkthrough
in the root README has something real to run against, and so the wiring sample has
a home you can copy.

```
demo/
  .claude/settings.json   the hook wiring: which guard runs before which tool
  guardrails.json         this project's rules, protected paths and allowlist
  rules/team-rules.md     a protected file — writes here need an approval token
  notes/scratch.md        an ordinary file — the file lock ignores it
```

`.claude/settings.json` is written the way an installed project looks: `tools/`
and `guardrails.json` sit at the project root, and `$CLAUDE_PROJECT_DIR` resolves
to it. To install the kit in your own repo, copy `tools/`, `guardrails.json` and
this `.claude/settings.json` to your project root and edit the config.

In this repository `tools/` lives one level up, so the walkthrough runs the guards
by path and points them at this workspace with `GUARDRAILS_PROJECT_DIR`.

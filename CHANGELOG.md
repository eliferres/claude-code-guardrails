# Changelog

Versions match the git tags.

## [1.2.0](https://github.com/eliferres/claude-code-guardrails/releases/tag/v1.2.0) - 2026-10-04

### Added
- Added shell-write protection: the command guard refuses a redirect, `tee`, `sed -i`, `cp` or `mv` into a file the protected-file lock covers, a route that walked past the lock because it only checks the Write and Edit tools.
- Added spelling-proof matching for the command word: a refused command written as `'rm'`, `r\m`, `$'rm'`, `/bin/Rm`, `/bin/r[m]`, `env bash`, `command rm`, `RM`, with `$IFS` separators, inside `sh -c` or `eval`, or through an alias defined in the same command is refused like the plain spelling.
- Added argument parsing for `rm` and `git push`: flags in any order, cluster or long form (`-r --force`, `-rv -f`, `--recursive -f`), git's global options (`-c k=v`, `-C dir`) and every way of forcing a push (`-f`, `-fu`, a `+main` refspec) match the shipped rules.
- Added temp-safe deletes: a recursive `rm` whose every path resolves inside `$TMPDIR`, `/tmp` or `/var/tmp` passes, switched on per rule with `"allow_in_temp": true` and on in the shipped recursive-delete rule.
- Added the syntax guard (`tools/syntax-guard.sh`, `claude-code-guardrails syntax-guard`): a Write or Edit that would leave a shell or Python file unparseable is refused before it lands, parsed by bash, zsh (by shebang, when zsh is installed) or Python, including Python embedded in a shell file under any `python3.X` name, where an apostrophe in a single-quoted `python3 -c` body passes `bash -n` and runs cut off.
- Added an optional git pre-push secret scan (`tools/pre-push-secret-scan.sh`, `claude-code-guardrails secret-scan`): a push whose commits add a vendor-prefixed key or token (cloud, code host, chat, payment, AI, package registry), a Slack webhook URL, a private key block or a generic `name = secret` literal is refused, naming the file and shape but never the value; fixture paths go in `.secret-scan-allow`.
- Added a decision log: every refusal by a guard is appended to `.guardrails/decisions.jsonl` with its rule id and subject, and one allowed call in `decision_log.sample_allow_every` (default 10) is logged too, so a rule that only ever fires on safe commands can be found and retired. The log is readable by its owner only, and a row that cannot be written prints one warning instead of failing silently.
- Added `pyproject.toml`, so `pipx install git+https://github.com/eliferres/claude-code-guardrails` installs a `claude-code-guardrails` command with `--version` and the seven existing jobs as subcommands.

### Changed
- Changed usage and configuration errors from `lock-approve`, `claims-clear`, `claims-takeover` and an unknown job to exit 2 instead of 1, so 1 always means findings.
- Listed the command guard's rules in a README table: each rule's id, what it refuses and why.
- Added license, Python and dependency badges beside the CI badge.
- Gave liveness and the file list their own README sections, put install first, and renamed the wiring section, so the reading order is install, guards, liveness, wiring, walkthrough.
- Renamed `tools/guardrails.py` to `tools/claude_code_guardrails.py`, so an install cannot shadow the `guardrails` package from guardrails-ai.

### Fixed
- Fixed the protected-file lock missing a protected file written in other letter case on macOS (`GUARDRAILS.JSON` for `guardrails.json`); on a Mac protected names now match without case.
- Fixed every hint and usage line naming a `tools/*.sh` script, which an installed user's project does not have: run from a clone they still name the script you ran, and run as the installed command they say `claude-code-guardrails lock-approve` and so on.
- Fixed guards going quiet on a `guardrails.json` that is missing, unreadable or malformed: they still let the call through, but now print one warning naming the config once and saying what was wrong with it.
- Re-rendered the demo image at a smaller type size, so the same session fits without rows running to the edge.
- Fixed the demo image check accepting a picture with output rows missing or out of order: it now walks the transcript in order, rebuilds each command from its rows, and requires every output line to be its own row.
- Fixed `demo/transcript.json`, which recorded exit code 1 for two commands that exit 0 and had an `exit` line typed into their output; it is now regenerated from a real run, and a test replays every entry and checks every row of the demo image against it.
- Fixed the README opener, which counted four hooks and called liveness the fourth guard: there are three guards, the shipped wiring runs those three plus a cleanup command when a session ends, and liveness is a separate check that proves the guards still block.
- Fixed the command guard reading a reserved word as the command name, so `{ git push origin +main; }`, `if true; then ...; fi` and `! git push origin +main` hid the command after `{`, `then` or `!` from every rule.
- Fixed a relative write passing unread after a branch, loop, brace group, `pushd`, `eval` or `source`, where the folder is unknown: `if true; then echo x > guardrails.json; fi` now refuses any such write whose name matches the last part of a protected pattern.
- Fixed heredoc delimiters that are not plain identifiers (`<<"E"OF`, `<<END-1`, `<<E\OF`, `<<EOF.txt`) never ending, which hid every command after the real end line: the delimiter is now the whole word with quotes removed, and the body ends only at a line exactly equal to it, as in bash.
- Fixed brace lists passing unread in write targets (`tee {guardrails.json,}`, `echo x > {guardrails.json,}`) and in `rm` and `git` arguments (`-{r,f}`, `{+main,}`): both are now brace-expanded before they are checked.
- Fixed a guard that crashed exiting 1, which Claude Code reads as allow: any error inside a guard now refuses the call with one line on stderr.
- Fixed shortened rm long options (`--rec -f`, `--recursiv --forc`) passing the recursive-delete rule: an unambiguous prefix now reads as the option it names, as GNU rm reads it.

## [1.1.0](https://github.com/eliferres/claude-code-guardrails/releases/tag/v1.1.0) - 2026-09-03

### Added
- Added a terminal demo to the README's first screen, showing command-guard blocking a recursive force-delete, clearing the one allowlisted build-cache path, and still blocking a neighbour path.
- Added CI runs on macos-latest alongside ubuntu-latest, which is what would have caught the fixture bug below.

### Changed
- Added full type hints to all 27 functions in guardrails.py, with no behavior change.
- Split `liveness()` from one 57-line function into read-manifest, check-guard, and print-report phases.

### Fixed
- Fixed fixture paths to resolve through realpath so the test suite passes on macOS, where test 7 compared an unresolved mktemp path against the guard's already-resolved target and never matched.

## [1.0.0](https://github.com/eliferres/claude-code-guardrails/releases/tag/v1.0.0) - 2026-08-31

First public release.

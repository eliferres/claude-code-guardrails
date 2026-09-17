# Changelog

Versions match the git tags.

## Unreleased

### Added
- Added `pyproject.toml`, so `pipx install git+https://github.com/eliferres/claude-code-guardrails` installs a `claude-code-guardrails` command with `--version` and the seven existing jobs as subcommands.

### Changed
- Gave liveness and the file list their own README sections, put install first, and renamed the wiring section, so the reading order is install, guards, liveness, walkthrough, wiring.
- Renamed `tools/guardrails.py` to `tools/claude_code_guardrails.py`, so an install cannot shadow the `guardrails` package from guardrails-ai.

### Fixed
- Fixed guards going quiet on a malformed or unreadable `guardrails.json`: they still let the call through, but now print one warning naming the config path and the parse error.
- Fixed `demo/transcript.json`, which recorded exit code 1 for two commands that exit 0 and had an `exit` line typed into their output; it is now regenerated from a real run, and a test replays every entry and checks every row of the demo image against it.
- Fixed the README opener, which counted four hooks: `guardrails.json` lists three guards, and the liveness harness is a separate check that proves they still block.

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

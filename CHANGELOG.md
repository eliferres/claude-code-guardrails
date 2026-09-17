# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- Added `pyproject.toml`, so `pipx install git+https://github.com/eliferres/claude-code-guardrails` installs a `claude-code-guardrails` command with `--version` and the seven existing jobs as subcommands.

### Changed
- Renamed `tools/guardrails.py` to `tools/claude_code_guardrails.py`, so an install cannot shadow the `guardrails` package from guardrails-ai.

### Fixed
- Fixed guards going quiet on a malformed or unreadable `guardrails.json`: they still let the call through, but now print one warning naming the config path and the parse error.

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

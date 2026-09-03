# Contributing

Welcome things:

- New command shapes, with the incident that motivated them in one line.
- Red cases for shapes that are blocked but under-tested.
- Fixes to anything the README claims that turns out not to be true.

**Every new guard ships with a red case.** A red case is a bash script that
drives exactly one blocked shape through `$GUARD` and exits 0 only if the guard
refused it. Add it to `tests/red/`, list it under that guard in
`guardrails.json`, and check `tools/liveness.sh` before you push: it runs your
case twice, once against the real guard and once against a stub that allows
everything, and fails if the case passes both times.

Ground rules: bash and the Python standard library only, `tests/run-tests.sh`
and `tools/liveness.sh` both green, and no guard that cannot explain its refusal
in a sentence a person can act on. Shape lists are deliberately a starting set.
If your proposal is a whole new category of guard, open an issue first.

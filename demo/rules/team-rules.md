# Team rules

A high-stakes file: the agent reads these rules every session, so an unreviewed
edit here changes how it behaves everywhere. `guardrails.json` lists `rules/*.md`
as protected, which is why writing to this file needs an approval token.

1. Ship one logical change per commit.
2. Never stage files you did not read.
3. Anything irreversible gets a human yes first.

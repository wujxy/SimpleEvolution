"""The scientist: a standalone, in-world research agent (one world, one agent).

``python -m scientist.cli --spec spec.json --world DIR`` drops a scientist
into a directory-world with its claude assistant beside it (one container,
one filesystem, equal capabilities) and walks itself to an exit
(deliver_world / abstain), leaving the wire log, research state, assistant
transcripts, usage, and conclusion.json under the world's ``.scientist/``.

Zero simpleevo imports. The supervisor generation that once lived here
(host/, assistant/, memory/) moved to ``simpleevo/`` next to its only
consumer; the dependency runs one way, simpleevo → scientist, over the
shared model transport, file-boundary plumbing, and research skills.
"""

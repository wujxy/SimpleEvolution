# wavegen_v1 — vendored snapshot of waverec's waveform generator

Copied verbatim from `benchmarks/waverec/wavegen/` at commit `8628c28`
("finish waverec bench") so that JunoResBench is self-contained and
bit-reproducible even if waverec evolves. waverec v1 is a frozen benchmark
("wavegen frozen once data is cut"), so the snapshot cannot drift silently.

Source file SHA256 at snapshot time:

| file | sha256 |
|---|---|
| config.py | 2f20211cd77d4d6420eb2a72926aabaf107b7c4589705cc6368bb74e50128045 |
| generator.py | b0f66709f65afbb595f9b59c9746b711447716a67c6d82687c965a234656127a |
| __init__.py | 203a63a61a8baad4cdeb8c606a4bedfe8ea999b6fe8031812ec636b6364d9174 |

Import via `juno_res_bench` (see `../__init__.py`), never by adding
`_vendor` to sys.path directly.

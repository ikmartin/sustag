"""build3 -- the data layer, rebuilt to `notes/book/data_chapter.md`.

Three layers plus a decisions input: `acquire/` owns every network call and raw byte and writes immutable snapshots; `derive/` is a pure function `build(snapshots, parameters, decisions) -> artifacts`; `publish/` classifies rather than drops. Human adjudications live in `decisions/` (tracked); intent lives in `parameters/` (tracked). `src/build2` is frozen reference material -- read it for a trap or a constant, never import it.
"""

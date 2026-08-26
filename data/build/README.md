# data/build — the pipeline runbook

The build is `build(snapshots, parameters, decisions)`; this file is the operating loop around it. The chapter (`notes/book/data_chapterv2.md`) is the binding spec; the plan (`notes/plan/build4_Aug25.md`) records how the rebuild landed.

## the loop

```
python -m data.build.run acquire            # a1 seed (once), a2 census + id mint, a3 fetch + snapshot cut, a4 verify + drift
python -m data.build.run build              # full A -> D -> P against the pinned snapshot
python -m data.build.run d3 --single        # exactly one stage
python -m data.build.run verify [stage] [--deep]
python -m data.build.review_panel           # decide every gated queue in one place
```

Stage map: a1 seed, a2 census (mints SNR ids), a3 bulk fetch (records / network / weather / attributes / facilities, ends by cutting a snapshot), a4 reconcile + units + drift; d1 extent, d2 canonicalisation (sentinels + ranges, cell-grain tallies), d3 geometry (snap + coord precision + the proximity table), d4 identity (type gate, evidence, assembly), d5 covariates + structures; p1 publication (partition + masks + the four leak scans).

A gate halting the build is a SystemExit naming outstanding ledger rows -- the build did what it could and is waiting on a person; decide in the review panel (writes land in `decisions/*.csv` immediately) and rerun. Blank decision = not reviewed, never approved; a decision made under a superseded criterion re-queues flagged with its prior verdict.

## the ledgers

All in `data/build/decisions/`, tracked, keyed on natural keys, added-to never rewritten:

| sheet | gates on | vocabulary |
|---|---|---|
| `drift.csv` | conflicting snapshot drift (a4) | `accept` / `reject` |
| `registry_gaps.csv` | unacknowledged source inconsistencies (a4) | `acknowledged` |
| `merge.csv` | gated merge proposals (d4) | `merge` / `distinct` |
| `site_type.csv` | recorded sensors with conflicting type evidence (d4) | the type vocabulary |
| `exclusions.csv` | rows a person opened (d4) | `exclude` / `keep` |
| `composed.csv` | multi-member compositions (d4) | `confirm` |
| `quality.csv` | flagged high-tier series (p1) | `keep` / `exclude` (+ `exclude_spans`, `max_threshold`) |
| `ids.csv` | never -- the append-only SNR id mint, written by a2 alone | (not a decision sheet) |

## stores (interim locations, until the final migration)

Raw + acquired archives: `src/data/{raw,acquired}` (adopted in place, snapshot-manifested). Everything the build writes: `data/stores/{snapshots,derived,published}`. The final migration moves the archives to `data/stores/` and flips the constants in `data/build/config.py` AND `data/access/config.py` -- both, they mirror each other deliberately.

## facts the loop rests on

- The census is the row authority (~20,233 registrations; 4,297 in the AOE arms beyond the seed boxes) and the ONLY minting step: SNR codes are minted once, appended to `decisions/ids.csv`, and never re-derived -- deleting that file is the one act that renumbers, which is why it is tracked.
- Every a3 pass ends by cutting a snapshot; a4 diffs HEAD against its parent on CONTENT probes (sha, parquet schema fingerprint, newest data timestamp), never mtime. A wholesale vendor replacement is one `adoption:`-noted cut, not hundreds of drift rows.
- The registry fingerprint (mappings, scales, sentinels, ranges, stats, combine) stamps every canonical file; editing the registry legitimately stales the whole canonical store.
- The fetch partition `registered = skipped + cached + source_empty + attempt_failed` is checked after every full a3; `source_empty` is durable, `attempt_failed` retries.
- The two canary probes (groundwater level, precipitation) must arrive registered, un-skipped and fetchable on every census -- a missing canary is a broken scope filter, not a data gap.

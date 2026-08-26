"""The one generic decision-ledger kit. Seven ledgers, zero copies.

build2 hand-copied the loader/pending/sync/gate quartet three times (merge, type, basin), differing only in vocabulary -- and the copies drifted, which is the duplicated-contract failure `OWNERS` exists to prevent, applied to code. Here a `Ledger` is declared once with its key columns, vocabulary and gating rule, and every behaviour comes from the kit.

THE SEMANTICS, SHARED BY ALL LEDGERS:
- A ledger is a tracked CSV of human adjudications, keyed on NATURAL KEYS (uids, pairs, (site, channel)) so decisions survive regeneration and reordering. Sheets are ADDED TO, NEVER REWRITTEN; a retired-but-decided row is kept unnumbered at the bottom (a later cohort can bring its case back); a retired-and-undecided row is dropped -- it reads as outstanding work nobody will ever do.
- BLANK MEANS NOT REVIEWED, NEVER APPROVED. An unrecognised decision token raises rather than being ignored. A decision is never seeded from the rule's proposal -- a pre-filled verdict is indistinguishable from a reviewed one.
- The number column is a POSITION rewritten on every sync (row N of the sheet is row N of the panel), never an identity.
- Where a rule settles a case unasked, the settlement goes to a companion AUTO RECORD so the silence is inspectable.
- `gate()` halts the build via SystemExit naming exactly the outstanding rows and where to answer them. A warning would be read once and scrolled past forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config

EDITABLE = ("decision", "decided_by", "note")


@dataclass(frozen=True)
class Ledger:
    """One decision ledger: its file, keys, vocabulary, and whether it gates."""

    name: str                       # e.g. "merge" -> decisions/merge.csv
    key_cols: tuple[str, ...]       # natural keys; NEVER a minted id that can drift
    vocabulary: tuple[str, ...]     # legal decision tokens
    number_col: str = "Row"         # the panel-position column
    gates: bool = True              # does an unanswered row halt the build?
    extra_editable: tuple[str, ...] = ()   # ledger-specific columns a human writes, preserved across syncs
    columns: tuple[str, ...] = ()   # sheet column order; defaults to number+keys+editables+evidence
    doc: str = ""

    @property
    def path(self) -> Path:
        return config.DECISIONS / f"{self.name}.csv"

    @property
    def auto_path(self) -> Path:
        return config.REVIEW_DIR / f"{self.name}_auto.csv"

    # ---- reading ---------------------------------------------------------------------------------

    def _editable(self) -> tuple[str, ...]:
        """Editable columns in sheet order: extras sit immediately right of `decision`, because they QUALIFY it -- a reviewer reads "keep ... minus these spans" as one statement, not two columns apart."""
        return ("decision", *self.extra_editable, "decided_by", "note")

    def load(self) -> pd.DataFrame:
        """Rows carrying a decision. Blank rows are NOT REVIEWED and are excluded here; unknown tokens raise."""
        if not self.path.exists():
            return pd.DataFrame(columns=[*self.key_cols, *self._editable()])
        d = pd.read_csv(self.path, dtype=str).fillna("")
        missing = [c for c in self.key_cols if c not in d.columns]
        if missing:
            raise ValueError(f"{self.path.name} lacks key column(s) {missing}")
        val = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
        bad = sorted(set(val[val != ""]) - set(self.vocabulary))
        if bad:
            raise ValueError(f"{self.path.name} has unrecognised decision(s) {bad}; "
                             f"expected one of {sorted(self.vocabulary)}")
        return d[val != ""].reset_index(drop=True)

    def decided_keys(self) -> set[tuple]:
        d = self.load()
        return {tuple(str(r[c]) for c in self.key_cols) for _, r in d.iterrows()}

    def decisions(self) -> dict[tuple, str]:
        d = self.load()
        return {tuple(str(r[c]) for c in self.key_cols): str(r["decision"]).strip()
                for _, r in d.iterrows()}

    # ---- syncing ---------------------------------------------------------------------------------

    def sync(self, ordered: pd.DataFrame) -> tuple[int, int, int]:
        """Reconcile the sheet with the current (already-ordered, already-gated) candidate set.

        Returns (added, kept, retired). `ordered` must hold only the rows a person is being asked about, in the order the panel will draw them -- the sheet-row/panel-row correspondence is this function's contract, not its concern.
        """
        cols = self.columns or (self.number_col, *self.key_cols, *self._editable(),
                                *[c for c in ordered.columns if c not in self.key_cols])
        key = lambda df: list(zip(*[df[c].astype(str) for c in self.key_cols])) if len(df) else []
        cur = ordered.copy()
        cur_keys = key(cur)

        prior, retired = {}, None
        if self.path.exists():
            old = pd.read_csv(self.path, dtype=str).fillna("")
            if all(c in old.columns for c in self.key_cols):
                for k, row in zip(key(old), old.to_dict("records")):
                    prior[k] = {c: row.get(c, "") for c in self._editable()}
                gone = old[[k not in set(cur_keys) for k in key(old)]]
                gone = gone[gone.get("decision", pd.Series("", index=gone.index)).astype(str).str.strip() != ""]
                retired = gone if len(gone) else None

        for c in self._editable():
            cur[c] = [prior.get(k, {}).get(c, "") for k in cur_keys]
        cur.insert(0, self.number_col, range(1, len(cur) + 1))
        out = cur[[c for c in cols if c in cur.columns]]

        if retired is not None:
            r = retired.copy()
            r[self.number_col] = ""            # unnumbered: not on the panel, not part of the ordering
            out = pd.concat([out, r[[c for c in cols if c in r.columns]]], ignore_index=True)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        out.to_csv(tmp, index=False)
        tmp.replace(self.path)
        added = sum(1 for k in cur_keys if k not in prior)
        return added, len(cur_keys) - added, 0 if retired is None else len(retired)

    # ---- gating ----------------------------------------------------------------------------------

    def pending(self, ordered: pd.DataFrame) -> list[tuple]:
        """Keys of gated rows with no decision on file, in review order."""
        if not len(ordered):
            return []
        done = self.decided_keys()
        keys = list(zip(*[ordered[c].astype(str) for c in self.key_cols]))
        return [k for k in keys if k not in done]

    def gate(self, ordered: pd.DataFrame, worth: dict[tuple, str] | None = None) -> None:
        """Halt via SystemExit when any gated row is unanswered, naming every row and what it is worth.

        `worth` optionally maps a key to one line of consequence ("holds 4,404 nitrate days") -- the reviewer needs to know what an answer buys before deciding how urgent the queue is.
        """
        if not self.gates:
            return
        pend = self.pending(ordered)
        if not pend:
            return
        lines = []
        for k in pend:
            tag = " : ".join(k)
            w = f"   [{worth[k]}]" if worth and k in worth else ""
            lines.append(f"    {tag}{w}")
        raise SystemExit(
            f"\nBuild awaiting {len(pend)} decision(s) in {self.path.name}:\n" + "\n".join(lines) +
            f"\n\nFill `decision` (one of {sorted(self.vocabulary)}) in {self.path} and rerun. "
            f"Blank means not reviewed; decisions are keyed on {list(self.key_cols)} and survive every rebuild.")

    # ---- the auto record -------------------------------------------------------------------------

    def record_auto(self, settled: pd.DataFrame) -> Path:
        """What the rule settled unasked. A record, not a queue -- but it must not be invisible."""
        self.auto_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auto_path.with_name(self.auto_path.name + ".tmp")
        settled.to_csv(tmp, index=False)
        tmp.replace(self.auto_path)
        return self.auto_path


def ledger_hash() -> str:
    """One hash over every tracked ledger -- the `ledgers` stamp on derived artifacts."""
    from . import contracts

    return contracts.ledger_hash(sorted(config.DECISIONS.glob("*.csv")))


# ---- the eight ledgers ----------------------------------------------------------------------------
# Declared here so the set is enumerable (the chapter's Gates-and-ledgers table, in code). Site-scoped
# ledgers key on the SORTED MEMBER-SENSOR SET ('uid_a+uid_b+...'), never the minted site id -- ids can
# drift a metre when a source revises a coordinate, and orphaning an answered row to a coordinate
# revision would re-ask questions that took real work. The site id is a display column.

DRIFT = Ledger("drift", ("source", "snapshot_pair", "diff_id"), ("accept", "reject"),
               doc="conflicting source drift: an authority changed something already relied upon")
REGISTRY_GAPS = Ledger("registry_gaps", ("source", "uid"), ("acknowledged",),
                       doc="a station one authority holds and another lacks (WQP0016)")
MERGE = Ledger("merge", ("uid_a", "uid_b"), ("merge", "distinct"),
               doc="bundle proposals touching a high-scrutiny record")
SITE_TYPE = Ledger("site_type", ("uid",), (),          # vocabulary injected from the type module at D3
                   doc="type decisions on recorded sensors with conflicting evidence")
EXCLUSIONS = Ledger("exclusions", ("uid",), ("exclude", "keep"),
                    doc="sensor excluded from every bundle: test loggers, scratch deployments")
BASINS = Ledger("basins", ("members",), (),            # vocabulary = basin methods, injected at D4
                gates=False,
                doc="delineation override per site (keyed on member set); advisory, never blocks")
SITE_REVIEW = Ledger("site_review", ("members",), ("confirm",),
                     doc="the COMPOSED multi-member site, accepted as one object. Pairwise merge answers "
                         "edit membership; this confirms what they compose -- the site the build will "
                         "actually mint, seen whole by a person. Keyed on the member set, so a membership "
                         "change is a NEW key and re-asks by construction; singletons never queue")
QUALITY = Ledger("quality", ("members", "channel"), ("keep", "exclude"),
                 extra_editable=("exclude_spans", "max_threshold"),
                 doc="per-(site, channel) series verdicts; high-tier channels gate. A `keep` with "
                     "`exclude_spans` (e.g. '2021-03-01..2021-07-15; 2022-01-02..2022-01-09') keeps the "
                     "series minus those day windows; a `keep` with `max_threshold` (a number, in the "
                     "channel's canonical units) nulls every published day whose readings exceed it -- a "
                     "SENSOR-specific ceiling, where the registry's `hi` is the channel-wide physical one. "
                     "Publication applies both and records what was removed")

ALL = (DRIFT, REGISTRY_GAPS, MERGE, SITE_TYPE, EXCLUSIONS, BASINS, QUALITY)

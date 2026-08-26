"""The one generic decision-ledger kit. Seven ledgers, zero copies.

A `Ledger` is declared once with its key columns, vocabulary, criterion version and gating rule, and every behaviour comes from the kit -- hand-copied loader/pending/sync/gate quartets are how copies drift.

THE SEMANTICS, SHARED BY ALL LEDGERS:
- A ledger is a tracked CSV of human adjudications, keyed on NATURAL KEYS (uids, pairs, (members, channel)) so decisions survive regeneration and reordering. Sheets are ADDED TO, NEVER REWRITTEN; a retired-but-decided row is kept unnumbered at the bottom (a later cohort can bring its case back); a retired-and-undecided row is dropped -- it reads as outstanding work nobody will ever do.
- BLANK MEANS NOT REVIEWED, NEVER APPROVED. An unrecognised decision token raises rather than being ignored. A decision is never seeded from the rule's proposal -- a pre-filled verdict is indistinguishable from a reviewed one.
- The number column is a POSITION rewritten on every sync (row N of the sheet is row N of the review panel), never an identity.
- EVERY DECISION RECORDS THE CRITERION VERSION it was answered under. When a ledger's criterion changes, rows decided under the old version become PENDING-FOR-CONFIRMATION: their text stays visible on the sheet, but they carry no authority -- `load()` excludes them, `pending()` re-queues them, and `gate()` names the prior verdict so reconfirmation is a glance, not a re-derivation. The review panel stamps the version at decide() time; a hand-edited row is stamped at the next sync, which mis-attributes only a row hand-decided in the window between a criterion change and its first sync -- the panel path has no such window.
- Where a rule settles a case unasked, the settlement goes to a companion AUTO RECORD so the silence is inspectable.
- `gate()` halts the build via SystemExit naming exactly the outstanding rows and where to answer them. A warning would be read once and scrolled past forever.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config

EDITABLE = ("decision", "decided_by", "note")
CRITERION_COL = "criterion_version"


class ConcurrentEditError(RuntimeError):
    """The sheet changed on disk between a read and a single-row write. Re-read and retry."""


@dataclass(frozen=True)
class Ledger:
    """One decision ledger: its file, keys, vocabulary, criterion version, and whether it gates."""

    name: str                       # e.g. "merge" -> decisions/merge.csv
    key_cols: tuple[str, ...]       # natural keys; NEVER a minted id that can drift
    vocabulary: tuple[str, ...]     # legal decision tokens
    number_col: str = "Row"         # the panel-position column
    gates: bool = True              # does an unanswered row halt the build?
    criterion: str = ""             # current criterion version; "" = unversioned (decisions never go stale)
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

    def _carried(self) -> tuple[str, ...]:
        """Columns that follow the key across syncs: the human-editable set plus the machine-stamped criterion version."""
        return (*self._editable(), CRITERION_COL)

    def read_all(self) -> pd.DataFrame:
        """The whole sheet as strings, decided or not -- the review panel's read path. Empty frame with key+carried columns when absent."""
        if not self.path.exists():
            return pd.DataFrame(columns=[self.number_col, *self.key_cols, *self._carried()])
        return pd.read_csv(self.path, dtype=str).fillna("")

    def _validated(self) -> pd.DataFrame:
        d = self.read_all()
        missing = [c for c in self.key_cols if c not in d.columns]
        if missing and len(d):
            raise ValueError(f"{self.path.name} lacks key column(s) {missing}")
        val = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
        bad = sorted(set(val[val != ""]) - set(self.vocabulary))
        if bad:
            raise ValueError(f"{self.path.name} has unrecognised decision(s) {bad}; "
                             f"expected one of {sorted(self.vocabulary)}")
        return d

    def load(self) -> pd.DataFrame:
        """Rows carrying a CURRENT decision. Blank rows are NOT REVIEWED; decided-under-an-old-criterion rows carry no authority and are excluded here (they re-queue via `pending()`); unknown tokens raise."""
        d = self._validated()
        if not len(d):
            return d
        val = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
        keep = val != ""
        if self.criterion:
            ver = d.get(CRITERION_COL, pd.Series("", index=d.index)).astype(str).str.strip()
            keep &= ver == self.criterion
        return d[keep].reset_index(drop=True)

    def stale_decisions(self) -> pd.DataFrame:
        """Rows decided under a SUPERSEDED criterion -- visible history awaiting reconfirmation."""
        d = self._validated()
        if not len(d) or not self.criterion:
            return d.iloc[0:0]
        val = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
        ver = d.get(CRITERION_COL, pd.Series("", index=d.index)).astype(str).str.strip()
        return d[(val != "") & (ver != self.criterion)].reset_index(drop=True)

    def decided_keys(self) -> set[tuple]:
        d = self.load()
        return {tuple(str(r[c]) for c in self.key_cols) for _, r in d.iterrows()}

    def decisions(self) -> dict[tuple, str]:
        d = self.load()
        return {tuple(str(r[c]) for c in self.key_cols): str(r["decision"]).strip()
                for _, r in d.iterrows()}

    # ---- single-row writes (the review panel's write path) ---------------------------------------

    def add_row(self, key: tuple, **evidence) -> None:
        """Open a question BY HAND: append one blank-decision row for `key`, creating the sheet if absent -- the pen for human-initiated ledgers (exclusions) that no build sync populates. Refuses a key already on the sheet; decide() then writes the verdict like any other row."""
        d = self.read_all() if self.path.exists() else pd.DataFrame()
        if len(d):
            have = {tuple(str(r[c]) for c in self.key_cols) for _, r in d.iterrows()}
            if tuple(map(str, key)) in have:
                raise ValueError(f"{key} is already on the {self.name} sheet")
        row = {self.number_col: len(d) + 1, **dict(zip(self.key_cols, map(str, key))),
               "decision": "", "decided_by": "", "note": "", CRITERION_COL: "", **evidence}
        cols = list(d.columns) if len(d) else [self.number_col, *self.key_cols, "decision",
                                               "decided_by", "note", CRITERION_COL,
                                               *[c for c in evidence]]
        out = pd.concat([d, pd.DataFrame([row])], ignore_index=True)[cols] if len(d)             else pd.DataFrame([row])[cols]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        out.to_csv(tmp, index=False)
        tmp.replace(self.path)

    def decide(self, key: tuple, decision: str, decided_by: str = "", note: str = "", **extras) -> None:
        """Write ONE row's verdict atomically: locate by natural key, set the editable cells, stamp the criterion version, replace the file.

        Vocabulary is validated here (blank = undo, restoring not-reviewed and clearing the stamp); extras must be declared `extra_editable` columns -- their VALUES are the caller's to validate, because the kit cannot know a span syntax from a ceiling. An mtime guard turns the one race (a build's sync interleaving with a panel write) into `ConcurrentEditError` instead of a lost update.
        """
        decision = str(decision).strip()
        if decision and decision not in self.vocabulary:
            raise ValueError(f"{decision!r} not in {sorted(self.vocabulary)}")
        undeclared = sorted(set(extras) - set(self.extra_editable))
        if undeclared:
            raise ValueError(f"{self.name} has no editable column(s) {undeclared}")
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} does not exist; decide() edits rows sync() created")

        before = self.path.stat().st_mtime_ns
        d = pd.read_csv(self.path, dtype=str).fillna("")
        mask = pd.Series(True, index=d.index)
        for c, v in zip(self.key_cols, key):
            mask &= d[c].astype(str) == str(v)
        hits = d.index[mask].tolist()
        if len(hits) != 1:
            raise KeyError(f"{self.path.name}: key {key} matches {len(hits)} row(s); decide() needs exactly one")
        i = hits[0]
        for col in (CRITERION_COL, *self._editable()):
            if col not in d.columns:
                d[col] = ""
        d.loc[i, "decision"] = decision
        d.loc[i, "decided_by"] = str(decided_by)
        d.loc[i, "note"] = str(note)
        for c, v in extras.items():
            d.loc[i, c] = str(v)
        d.loc[i, CRITERION_COL] = self.criterion if decision else ""

        if self.path.stat().st_mtime_ns != before:
            raise ConcurrentEditError(f"{self.path.name} changed on disk during decide(); re-read and retry")
        tmp = self.path.with_name(self.path.name + ".tmp")
        d.to_csv(tmp, index=False)
        os.replace(tmp, self.path)

    def decide_all(self, decision: str, decided_by: str = "", note: str = "") -> int:
        """Set EVERY row's verdict in one atomic write; `decision=""` clears the sheet back to not-reviewed, INCLUDING the extra editable cells (exclude_spans, max_threshold, ...).

        A bulk counterpart to `decide`, and deliberately a separate method: `decide` is the reviewed path and refuses anything but a single located row, while this one is the blunt instrument a panel offers behind a guard. One rewrite rather than N -- decide() rewrites the whole CSV per row, so 136 rows would be 136 full rewrites and 136 mtime races with each other. A bulk VERDICT leaves the extra cells alone (a keep-all should not silently discard hand-entered spans); only the reset wipes them, because a not-reviewed row with a leftover ceiling is a half-decision nobody made.
        """
        decision = str(decision).strip()
        if decision and decision not in self.vocabulary:
            raise ValueError(f"{decision!r} not in {sorted(self.vocabulary)}")
        if not self.path.exists():
            return 0
        before = self.path.stat().st_mtime_ns
        d = pd.read_csv(self.path, dtype=str).fillna("")
        if not len(d):
            return 0
        for col in (CRITERION_COL, *self._editable()):
            if col not in d.columns:
                d[col] = ""
        d["decision"] = decision
        d["decided_by"] = str(decided_by)
        d["note"] = str(note)
        if not decision:
            for col in self.extra_editable:
                d[col] = ""
        d[CRITERION_COL] = self.criterion if decision else ""
        if self.path.stat().st_mtime_ns != before:
            raise ConcurrentEditError(f"{self.path.name} changed on disk during decide_all(); re-read and retry")
        tmp = self.path.with_name(self.path.name + ".tmp")
        d.to_csv(tmp, index=False)
        os.replace(tmp, self.path)
        return len(d)

    # ---- syncing ---------------------------------------------------------------------------------

    def sync(self, ordered: pd.DataFrame) -> tuple[int, int, int]:
        """Reconcile the sheet with the current (already-ordered, already-gated) candidate set.

        Returns (added, kept, retired). `ordered` must hold only the rows a person is being asked about, in the order the panel will draw them -- the sheet-row/panel-row correspondence is this function's contract, not its concern. Rows carrying a decision but no criterion stamp (hand edits between builds) are stamped with the CURRENT version here.
        """
        cols = self.columns or (self.number_col, *self.key_cols, *self._carried(),
                                *[c for c in ordered.columns if c not in self.key_cols])
        key = lambda df: list(zip(*[df[c].astype(str) for c in self.key_cols])) if len(df) else []
        cur = ordered.copy()
        cur_keys = key(cur)

        prior, retired = {}, None
        if self.path.exists():
            old = pd.read_csv(self.path, dtype=str).fillna("")
            if all(c in old.columns for c in self.key_cols):
                for k, row in zip(key(old), old.to_dict("records")):
                    prior[k] = {c: row.get(c, "") for c in self._carried()}
                gone = old[[k not in set(cur_keys) for k in key(old)]]
                gone = gone[gone.get("decision", pd.Series("", index=gone.index)).astype(str).str.strip() != ""]
                retired = gone if len(gone) else None

        for c in self._carried():
            cur[c] = [prior.get(k, {}).get(c, "") for k in cur_keys]
        # stamp hand-edited rows: decided, no version on record, current criterion assumed
        if self.criterion:
            decided = cur["decision"].astype(str).str.strip() != ""
            blank_ver = cur[CRITERION_COL].astype(str).str.strip() == ""
            cur.loc[decided & blank_ver, CRITERION_COL] = self.criterion
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
        """Keys of gated rows with no CURRENT decision, in review order -- stale-criterion rows re-queue here."""
        if not len(ordered):
            return []
        done = self.decided_keys()
        keys = list(zip(*[ordered[c].astype(str) for c in self.key_cols]))
        return [k for k in keys if k not in done]

    def gate(self, ordered: pd.DataFrame, worth: dict[tuple, str] | None = None) -> None:
        """Halt via SystemExit when any gated row is unanswered, naming every row, what it is worth, and any superseded verdict awaiting reconfirmation."""
        if not self.gates:
            return
        pend = self.pending(ordered)
        if not pend:
            return
        stale = {tuple(str(r[c]) for c in self.key_cols): (str(r["decision"]), str(r.get(CRITERION_COL, "")))
                 for _, r in self.stale_decisions().iterrows()}
        lines = []
        for k in pend:
            tag = " : ".join(k)
            w = f"   [{worth[k]}]" if worth and k in worth else ""
            s = (f"   [was {stale[k][0]!r} under criterion {stale[k][1] or '?'} -- reconfirm]"
                 if k in stale else "")
            lines.append(f"    {tag}{w}{s}")
        raise SystemExit(
            f"\nBuild awaiting {len(pend)} decision(s) in {self.path.name}:\n" + "\n".join(lines) +
            f"\n\nDecide via the review panel (python -m data.build.review_panel) or fill `decision` "
            f"(one of {sorted(self.vocabulary)}) in {self.path} and rerun. Blank means not reviewed; "
            f"decisions are keyed on {list(self.key_cols)} and survive every rebuild.")

    # ---- the auto record -------------------------------------------------------------------------

    def record_auto(self, settled: pd.DataFrame) -> Path:
        """What the rule settled unasked. A record, not a queue -- but it must not be invisible."""
        self.auto_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.auto_path.with_name(self.auto_path.name + ".tmp")
        settled.to_csv(tmp, index=False)
        tmp.replace(self.auto_path)
        return self.auto_path


def ledger_hash() -> str:
    """One hash over every tracked ledger (the id ledger included) -- the `ledgers` stamp on derived artifacts."""
    from . import contracts

    return contracts.ledger_hash(sorted(config.DECISIONS.glob("*.csv")))


# ---- the seven ledgers ----------------------------------------------------------------------------
# Declared here so the set is enumerable (the chapter's Gates-and-ledgers table, in code). Bundle-scoped
# ledgers key on the SORTED MEMBER-SENSOR SET ('uid_a+uid_b+...'), never a published id -- membership IS
# the natural key, and a membership change re-asks by construction. Criterion strings name the rule
# version decisions are made under; bump one when its rule changes and the old decisions re-queue.

DRIFT = Ledger("drift", ("source", "snapshot_pair", "diff_id"), ("accept", "reject"),
               doc="conflicting source drift: an authority changed something already relied upon")
REGISTRY_GAPS = Ledger("registry_gaps", ("source", "uid"), ("acknowledged",),
                       doc="a station one authority holds and another lacks")
MERGE = Ledger("merge", ("uid_a", "uid_b"), ("merge", "distinct"),
               criterion="prox100-v1",
               doc="merge proposals touching a high-scrutiny record. Criterion prox100-v1: candidates from "
                   "the proximity table, uniform min_sep_m <= 100 m gate, two-tier type rule, 4-branch ladder")
SITE_TYPE = Ledger("site_type", ("uid",), (),          # vocabulary injected from the type module at D4
                   criterion="type-v1",
                   doc="type decisions on recorded sensors with conflicting evidence")
EXCLUSIONS = Ledger("exclusions", ("uid",), ("exclude", "keep"),
                    doc="sensor excluded from every bundle: test loggers, scratch deployments")
COMPOSED = Ledger("composed", ("members",), ("confirm",),
                  criterion="prox100-v1",
                  doc="the COMPOSED multi-member bundle, accepted as one object. Pairwise merge answers "
                      "edit membership; this confirms what they compose, seen whole by a person. Keyed on "
                      "the member set, so a membership change is a NEW key and re-asks by construction; "
                      "singletons never queue")
QUALITY = Ledger("quality", ("members", "channel"), ("keep", "exclude"),
                 criterion="qual-v1",
                 extra_editable=("exclude_spans", "max_threshold"),
                 doc="per-(record, channel) series verdicts; high-tier channels gate. A `keep` with "
                     "`exclude_spans` (e.g. '2021-03-01..2021-07-15; 2022-01-02..2022-01-09') keeps the "
                     "series minus those day windows; a `keep` with `max_threshold` (a number, in the "
                     "channel's canonical units) nulls every published day whose readings exceed it -- a "
                     "SENSOR-specific ceiling, where the registry's `hi` is the channel-wide physical one. "
                     "Publication applies both and records what was removed")

ALL = (DRIFT, REGISTRY_GAPS, MERGE, SITE_TYPE, EXCLUSIONS, COMPOSED, QUALITY)
if len({led.name for led in ALL}) != len(ALL):
    raise ValueError("ledger names collide")

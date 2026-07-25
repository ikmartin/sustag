"""render_results.py -- unified feature-manifest report for one run.

Reads the unified log.json (every entry tagged with run_id + mode) and renders ONE run to
results_{run_id}.md. Per task the report has three parts, synergy first:

  Synergy cross-tab  each feature's add-one Δ (over base) vs drop-one Δ (from full); the join.
  Add-one            scoreboard / scores / deltas / added-feature importance.
  Drop-one           scoreboard / scores / deltas (leave-one-out from full).

Every table is preceded by a one-sentence caption. Judge on LOFO (lofo_r2/lofo_auc); the base holds
the tot_* fingerprints so LOSO deltas are muted, and sub-~0.005 Δ is noise.

    python render_results.py                 # list valid run_ids
    python render_results.py <run_id>        # render that run -> results_{run_id}.md
    python render_results.py latest          # render the newest run
"""

import json
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_LOG = _HERE / "log.json"

# metric to rank add_* rows by, per task (honest transfer headline)
_RANK_METRIC = {"reg": "lofo_r2", "clf": "lofo_auc"}
# structural score keys shown but not treated as deltas-of-interest (still deltated for completeness)
_COUNT_KEYS = ("n_sites", "n_families", "n_rows", "n_feat")

# scoreboard: rank-by metric (rank 1 = largest delta) + the delta columns shown, per task
_SCOREBOARD = {
    "reg": {"rank_by": "between_r2", "cols": ["lofo_r2", "between_r2", "within_r2"]},
    "clf": {"rank_by": "lofo_auc", "cols": ["lofo_auc", "lofo_prauc", "between_rate_r2"]},
}


def _fmt(v):
    """Compact cell: ints as-is, whole floats -> int, other floats -> 4dp, NaN -> 'NaN'."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.4f}"
    return str(v)


def _table(df: pd.DataFrame, index_label: str) -> str:
    """Markdown table with the row index as a leading labelled column, cells via _fmt."""
    header = [index_label] + [str(c) for c in df.columns]
    lines = [header, ["---"] * len(header)]
    for idx, row in df.iterrows():
        lines.append([str(idx)] + [_fmt(v) for v in row])
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)


def _added_feature(name: str) -> str:
    """'add_Corn' -> 'Corn'; 'base' -> ''."""
    return name[len("add_") :] if name.startswith("add_") else ""


def _added_importance(entry: dict, base_feats: set, key: str) -> float:
    """Sum the added feature's importance across whatever columns it introduced -- i.e. this recipe's
    feature columns that aren't in the base. Handles bucketed families, multi-column harmonics, and
    single columns uniformly (added = recipe features - base features)."""
    imp = entry.get(key, {}) or {}
    added = [c for c in imp if c not in base_feats]
    return float(sum(imp[c] for c in added)) if added else float("nan")


def _sum_over(imp: dict, cols: list) -> float:
    """Sum an importance dict over the given columns (NaN if none are present)."""
    vals = [imp[c] for c in cols if c in imp]
    return float(sum(vals)) if vals else float("nan")


def _scoreboard(task: str, base: dict, adds: list[dict], full: dict | None) -> str:
    """Compact ranked summary: one row per candidate, ranked by the task's headline delta (rank 1 =
    largest), sorted 1st-place-first. REG ranks on lofo_r2 Δ; CLF ranks on lofo_prauc Δ. When a
    full-feature-set run is present, two extra columns show that feature's gain/perm IN THE FULL
    MODEL (all features present) -- the collinearity-inclusive view, next to the add-one marginal."""
    cfg = _SCOREBOARD[task]
    rank_by, cols = cfg["rank_by"], cfg["cols"]
    base_feats = set(base["features"])
    full_gain = (full or {}).get("importance", {}) or {}
    full_perm = (full or {}).get("importance_perm", {}) or {}

    def delta(e, m):
        return e["score"][m] - base["score"][m]

    def sort_key(e):
        d = delta(e, rank_by)
        return d if d == d else -1e9  # NaN sinks to the bottom

    ranked = sorted(adds, key=sort_key, reverse=True)
    colnames = [f"{m} ∆" for m in cols]
    rows = {}
    for i, e in enumerate(ranked, 1):
        added = [c for c in e["features"] if c not in base_feats]  # this candidate's columns
        row = {"Rank": i, **{f"{m} ∆": delta(e, m) for m in cols}}
        if full is not None:
            row["full_gain"] = _sum_over(full_gain, added)
            row["full_perm"] = _sum_over(full_perm, added)
        rows[_added_feature(e["name"])] = row
    extra = ["full_gain", "full_perm"] if full is not None else []
    df = pd.DataFrame(rows).T[["Rank"] + colnames + extra]
    df["Rank"] = df["Rank"].astype(int)
    return _table(df, "Feature")


def _captioned(heading: str, caption: str, table: str) -> str:
    """A '### heading', a one-sentence italic caption, then the table."""
    return f"### {heading}\n\n_{caption}_\n\n{table}"


def _add_one_tables(task: str, entries: list[dict]) -> str:
    """Add-one sub-report: scoreboard / scores / deltas / added-feature importance, each captioned."""
    by_name = {e["name"]: e for e in entries}
    if "base" not in by_name:
        return "_(add-one: no base recipe logged)_"
    base, full = by_name["base"], by_name.get("full_feature_set")
    rank = _RANK_METRIC[task]
    adds = [e for e in entries if e["name"] not in ("base", "full_feature_set")]
    adds.sort(
        key=lambda e: (e["score"].get(rank) if e["score"].get(rank) == e["score"].get(rank) else -1e9), reverse=True
    )
    ordered = [base] + adds + ([full] if full else [])
    metrics = list(base["score"].keys())
    base_feats = set(base["features"])
    delta_metrics = [m for m in metrics if m not in _COUNT_KEYS]

    score_df = pd.DataFrame({e["name"]: e["score"] for e in ordered}).T[metrics]
    delta_df = pd.DataFrame({e["name"]: {m: e["score"][m] - base["score"][m] for m in delta_metrics} for e in adds}).T[
        delta_metrics
    ]
    imp_df = pd.DataFrame(
        {
            e["name"]: {
                "gain": _added_importance(e, base_feats, "importance"),
                "perm": _added_importance(e, base_feats, "importance_perm"),
            }
            for e in adds
        }
    ).T[["gain", "perm"]]
    return "\n\n".join(
        [
            _captioned(
                f"Add-one · Scoreboard (ranked by {rank} Δ)",
                "Each candidate's marginal LOFO gain added ALONE over the base (Δ = add − base), best first; full_gain/full_perm are its importance in the all-features model.",
                _scoreboard(task, base, adds, full),
            ),
            _captioned(
                "Add-one · Scores",
                "Raw CV scores per recipe — base, each add_<feature>, and the full-feature-set reference (last row).",
                _table(score_df, "recipe"),
            ),
            _captioned(
                "Add-one · Deltas vs base",
                "Every metric's change from base when each feature is added alone (add − base).",
                _table(delta_df, f"recipe (Δ vs base, {rank}-ranked)"),
            ),
            _captioned(
                "Add-one · Added-feature importance",
                "The added feature's gain and permutation importance in its own add-one model, summed over its distance buckets.",
                _table(imp_df, "recipe (added feature)"),
            ),
        ]
    )


def _drop_one_tables(task: str, entries: list[dict]) -> str:
    """Drop-one sub-report: scoreboard / scores / deltas, each captioned."""
    by_name = {e["name"]: e for e in entries}
    full = by_name.get("full_feature_set")
    if full is None:
        return "_(drop-one: no full_feature_set logged)_"
    drops = [e for e in entries if e["name"].startswith("drop_")]
    rank = _RANK_METRIC[task]
    full_score, full_feats = full["score"], set(full["features"])
    full_gain = full.get("importance", {}) or {}

    def delta(e, m):
        return e["score"][m] - full_score[m]

    drops.sort(key=lambda e: (delta(e, rank) if delta(e, rank) == delta(e, rank) else 1e9))  # most negative first
    cols = _SCOREBOARD[task]["cols"]
    board_rows = {}
    for i, e in enumerate(drops, 1):
        dropped = [c for c in full_feats if c not in set(e["features"])]
        board_rows[e["name"][len("drop_") :]] = {
            "Rank": i,
            **{f"{m} ∆": delta(e, m) for m in cols},
            "full_gain": _sum_over(full_gain, dropped),
        }
    board = pd.DataFrame(board_rows).T[["Rank"] + [f"{m} ∆" for m in cols] + ["full_gain"]]
    board["Rank"] = board["Rank"].astype(int)

    metrics = list(full_score.keys())
    score_df = pd.DataFrame({e["name"]: e["score"] for e in [full] + drops}).T[metrics]
    delta_metrics = [m for m in metrics if m not in _COUNT_KEYS]
    delta_df = pd.DataFrame({e["name"]: {m: delta(e, m) for m in delta_metrics} for e in drops}).T[delta_metrics]
    return "\n\n".join(
        [
            _captioned(
                f"Drop-one · Scoreboard (ranked by {rank} drop Δ)",
                "LOFO loss when each feature is removed from the full model (Δ = drop − full); most load-bearing (most negative) first; full_gain is its gain in the full model.",
                _table(board, "Dropped feature"),
            ),
            _captioned(
                "Drop-one · Scores",
                "Raw CV scores per recipe — the full model (first row) and each drop_<feature>.",
                _table(score_df, "recipe"),
            ),
            _captioned(
                "Drop-one · Deltas vs full",
                "Every metric's change from the full model when each feature is removed (drop − full).",
                _table(delta_df, f"recipe (Δ vs full, {rank}-ranked)"),
            ),
        ]
    )


def _synergy_class(a: float, d: float, t: float = 0.005) -> str:
    """Quadrant tag from add-one Δ (a) and drop-one Δ (d) at threshold t."""
    if a != a or d != d:
        return "n/a"
    if a <= t and d <= -t:
        return "SYNERGY"  # weak/neg alone, load-bearing in company
    if a >= t and d <= -t:
        return "robust"  # helps alone AND load-bearing
    if a <= -t and d >= t:
        return "harmful"  # hurts alone AND full is better without it
    if a >= t and d >= -t:
        return "redundant"  # helps alone, covered in full
    return "inert"


def _synergy_section(task: str, add_by_name: dict, drop_by_name: dict) -> str:
    """The unification: each feature's add-one Δ (over base) vs drop-one Δ (from full)."""
    rank = _RANK_METRIC[task]
    base, full = add_by_name.get("base"), drop_by_name.get("full_feature_set")
    if base is None or full is None:
        return _captioned(
            "Synergy cross-tab", "requires both an add-one base and a drop-one full model.", "_(missing base or full)_"
        )
    add_feats = {n[len("add_") :] for n in add_by_name if n.startswith("add_")}
    drop_feats = {n[len("drop_") :] for n in drop_by_name if n.startswith("drop_")}
    rows = {}
    for f in add_feats & drop_feats:
        a = add_by_name[f"add_{f}"]["score"][rank] - base["score"][rank]
        d = drop_by_name[f"drop_{f}"]["score"][rank] - full["score"][rank]
        rows[f] = {f"add_Δ ({rank})": a, f"drop_Δ ({rank})": d, "class": _synergy_class(a, d)}
    if not rows:
        return _captioned("Synergy cross-tab", "no features overlap between add-one and drop-one.", "_(no overlap)_")
    dropcol = f"drop_Δ ({rank})"
    ordered = sorted(rows.items(), key=lambda kv: (kv[1][dropcol] if kv[1][dropcol] == kv[1][dropcol] else 1e9))
    df = pd.DataFrame({f: v for f, v in ordered}).T[[f"add_Δ ({rank})", dropcol, "class"]]
    cap = (
        "Each feature's marginal at both ends — add_Δ (added alone over base) vs drop_Δ (removed from full), "
        f"on {rank}. Small/≤0 add_Δ with a strongly negative drop_Δ = **SYNERGY** (load-bearing only in "
        "company); classes tagged at |Δ|≈0.005; ranked most-load-bearing first."
    )
    foot = (
        f"\n\n_Add-only (screened but not in full, so no drop counterpart): "
        f"{', '.join(sorted(add_feats - drop_feats)) or 'none'}. "
        f"Drop-only (base features, always present so never added): {', '.join(sorted(drop_feats - add_feats)) or 'none'}._"
    )
    return _captioned("Synergy cross-tab (add-one vs drop-one)", cap, _table(df, "Feature")) + foot


def _run_task_section(task: str, entries: list[dict]) -> str:
    """One task's block: synergy first, then add-one and drop-one sub-reports (whichever are present)."""
    te = [e for e in entries if e["task"] == task]
    add = {e["name"]: e for e in te if e.get("mode") == "add_one"}
    drop = {e["name"]: e for e in te if e.get("mode") == "drop_one"}
    parts = [f"## {task.upper()}"]
    if add and drop:
        parts.append(_synergy_section(task, add, drop))
    if add:
        parts.append(_add_one_tables(task, list(add.values())))
    if drop:
        parts.append(_drop_one_tables(task, list(drop.values())))
    return "\n\n".join(parts)


def _run_index(log: dict) -> dict:
    """{run_id -> {tasks, modes, n, ts}} summarising each run in the log."""
    from collections import defaultdict

    info = defaultdict(lambda: {"tasks": set(), "modes": set(), "n": 0, "ts": ""})
    for e in log.values():
        d = info[e.get("run_id", "(legacy)")]
        d["tasks"].add(e["task"])
        d["modes"].add(e.get("mode", "?"))
        d["n"] += 1
        d["ts"] = max(d["ts"], e.get("timestamp", ""))
    return info


def render(run_id: str | None = None):
    if not _LOG.exists() or _LOG.stat().st_size == 0:
        raise SystemExit(f"no log at {_LOG} -- run run_exp.py first")
    log = json.loads(_LOG.read_text())
    info = _run_index(log)
    if run_id is None:  # require an id; with none, list valid ids
        print("valid run_ids (newest first) -- render with:  python render_results.py <run_id>")
        for rid in sorted(info, reverse=True):
            d = info[rid]
            print(f"  {rid:<26} tasks={sorted(d['tasks'])} modes={sorted(d['modes'])} entries={d['n']} last={d['ts']}")
        return
    if run_id == "latest":
        run_id = max(info)
    if run_id not in info:
        raise SystemExit(f"unknown run_id {run_id!r}. Valid: {sorted(info, reverse=True)}")
    entries = [e for e in log.values() if e.get("run_id") == run_id]
    tasks = sorted({e["task"] for e in entries})
    header = (
        f"# Feature-manifest report — run `{run_id}`\n\n"
        "Per task: a **synergy cross-tab** (add-one Δ vs drop-one Δ), then the full **add-one** screen "
        "and **drop-one** leave-one-out. Judge on LOFO (lofo_r2/lofo_auc); sub-~0.005 Δ is noise.\n\n"
    )
    out = _HERE / f"results_{run_id}.md"
    out.write_text(header + "\n---\n\n".join(_run_task_section(t, entries) for t in tasks) + "\n")
    print(f"wrote {out}  (run {run_id}, tasks {tasks}, {len(entries)} entries)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?", default=None, help="run id to render (or 'latest'); omit to list valid ids")
    render(ap.parse_args().run_id)

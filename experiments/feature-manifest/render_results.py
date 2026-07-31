"""render_results.py -- unified feature-manifest report for one run.

Reads the unified log.json (every entry tagged with run_id + mode) and renders ONE run to results_{run_id}.md. Per task, the conclusion first and the evidence under it:

  Shipped-recipe gap  where the SHIPPED recipe and this run's evidence disagree, in both directions -- units it omits that pay their way, units it carries that do not. The manifest's actual product: the screen is a test of feature usefulness INDEPENDENT of what ships, so `full` is deliberately a superset of the shipped recipe and `shipped` is only a label.
  Reference scores    the two models everything else is a delta against: base and full_feature_set. The only raw scores in the report.
  Add-one             every candidate added ALONE over the base, one table.
  Drop-one            every unit removed from the full model, one table.

Deltas are bolded when they clear the task's MEASURED single-run noise floor (_FLOOR); anything unbolded is not a result. A unit of one or two columns cannot clear that floor in either direction and is reported as unmeasurable rather than inert -- the distinction between "we tested it and it does nothing" and "this cohort cannot tell".

Every table is preceded by a one-sentence caption. Judge on LOFO; the base holds the tot_* fingerprints, so LOSO deltas are muted.

    python render_results.py                 # list valid run_ids
    python render_results.py <run_id>        # render that run -> results_{run_id}.md
    python render_results.py latest          # render the newest run

SCORE COLUMNS (2026-07-27 rework -- see METRICS.md for the derivation, lean_cook._cross_metrics for the code)

Every aggregate is computed from the LOFO out-of-fold predictions and carries the `lofo_` prefix. They used to come from LOSO, which made every diagnostic optimistic: LOSO and LOFO anti-correlate across recipes (REG -0.29, CLF -0.48), so LOSO is not a gentler LOFO but a misleading one. Exactly one LOSO column survives per task, purely so the LOSO-LOFO gap can be read as a leakage gauge.

Shared (both tasks):
  n_sites, n_families, n_rows, n_feat   structural counts. n_families is the number of LOFO groups -- low means a noisy LOFO.

CLF (8 metric columns):
  loso_auc               ROC-AUC on the site-grouped OOF. Leakage gauge ONLY; never rank on it.
  lofo_prauc             HEADLINE. Average precision on the family-grouped OOF. Load-bearing name: _RANK_METRIC ranks CLF by it.
  lofo_auc               ROC-AUC on the family-grouped OOF.
  lofo_prauc_lift        lofo_prauc / base rate; 1.0 = chance, so it stays comparable when the fleet's base rate moves. Replaced lofo_prauc, whose raw value is only readable against a fixed prevalence.
  lofo_recall_at_f2      Recall at the threshold maximising F2, i.e. the DEPLOYED operating point. Replaced recall_at_far, which read the ROC curve at FPR <= 10% -- a regime ~4x stricter than what ships.
  lofo_fdr_at_f2         False-discovery rate FP/(TP+FP) at that same threshold. PREVALENCE-DEPENDENT: always read it against `base`. Not the same as FAR/FPR, which is prevalence-invariant.
  lofo_brier             Calibration of P(violation). Reference is the climatology forecast at base*(1-base).
  base                   Violation base rate. The chance floor for lift, and the prevalence at which fdr is quoted.
  lofo_between_rate_r2   R2 of per-site mean predicted probability vs per-site actual violation rate -- can the model rank which ungauged basins are worst? The project's stated bottleneck.
  lofo_macro_auc         Median per-site AUC, equal weight per site. A large pooled/macro gap means performance is concentrated in the long-record sites.

REG (6 metric columns):
  loso_r2                R2 on the site-grouped OOF. Leakage gauge ONLY.
  lofo_r2                R2 on the family-grouped OOF. Load-bearing name: _RANK_METRIC ranks by it.
  lofo_rmse              Human-readable mg/L. A strictly decreasing function of lofo_r2 on a fixed row set (RMSE = sigma_y * sqrt(1 - R2)), so it carries ZERO ranking information -- report it, never rank on it.
  lofo_between_r2        R2 of predicted vs actual per-site means. Ranks site levels.
  lofo_within_r2         R2 after removing each site's mean from both truth and prediction. Tracks daily movement.
  lofo_macro_r2          Median per-site R2, equal weight per site.

DROPPED, and why (they will not appear in any run from 2026-07-27 onward):
  loso_prauc / loso_f1 / loso_f2 / loso_mcc / loso_recall_at_far   correlate >=0.91 with loso_auc; one LOSO column is enough for the gap.
  lofo_f1                max-F1 cherry-picks a threshold AND weights precision == recall, contradicting the beta=2 deployment.
  lofo_mcc / lofo_f2     0.75-0.95 correlated with the rest of the LOFO block.
  persist_skill          an exact monotone transform of loso_r2 (rho = +-1.00), and its predict-yesterday baseline needs a history a virtual site does not have.
  spearman               0.92 with loso_r2.
  brier / between_r2 / within_r2 / macro_r2 / between_rate_r2 / rmse (unprefixed)   the old LOSO-computed forms; superseded by the lofo_ versions above.

NOT emitted here: the lodo{d}_* block (LODO_d holdout). It is a true leave-one-out -- one fold per site, 123 vs 5 -- so ~25x the fits, which is prohibitive across ~117 recipes. Run it from src/eval/cook.py on a shortlist instead.
"""

import json
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_LOG = _HERE / "log.json"

# metric to rank add_* rows by, per task (honest transfer headline)
_RANK_METRIC = {"reg": "lofo_r2", "clf": "lofo_prauc"}
# structural score keys shown but not treated as deltas-of-interest (still deltated for completeness). `mean_best_iter` is a legacy key: runs before 2026-07-28 recorded it, and it is listed so those entries still render without it being read as a metric.
_COUNT_KEYS = ("n_sites", "n_families", "n_rows", "n_feat", "mean_best_iter")

# The delta columns shown per task. `rank_by` is deliberately _RANK_METRIC[task] -- the tables used to sort on _SCOREBOARD's own key while the heading was generated from _RANK_METRIC, so REG's add-one heading said "ranked by lofo_r2" over rows ordered by lofo_between_r2. One key, named honestly.
_SCOREBOARD = {
    "reg": {"cols": ["lofo_r2", "lofo_between_r2", "lofo_within_r2"]},
    "clf": {"cols": ["lofo_prauc", "lofo_auc", "lofo_prauc_lift", "lofo_between_rate_r2"]},
}

# Measured single-run noise on the headline metric -- an add-one delta smaller than this is not a result. From the manifest's own replicate spread (see experiments/specific-small-experiments/exps.py): REG add 0.0085, CLF add 0.0037. Everything the report calls significant, inert or harmful is judged against these.
_FLOOR = {"reg": 0.0085, "clf": 0.0037}
_TAIL = 12  # rows shown at each end of a long table before the middle is folded away

# Pre-rework name for each current column, so runs logged before 2026-07-27 still render. The old names are the LOSO-computed forms (see the DROPPED list in the module docstring), so a legacy render is NOT comparable to a current one -- it is readable history, nothing more.
_LEGACY_ALIAS = {
    "lofo_between_r2": "between_r2",
    "lofo_within_r2": "within_r2",
    "lofo_between_rate_r2": "between_rate_r2",
    "lofo_rmse": "rmse",
    "lofo_macro_auc": "macro_auc",
    "lofo_macro_r2": "macro_r2",
    "lofo_brier": "brier",
    # renamed 2026-07-28: the old names did not say WHICH beta (2.0)
    "lofo_recall_at_f2": "lofo_recall_at_beta",
    "lofo_fdr_at_f2": "lofo_fdr_at_beta",
}


def _sub(a, b) -> float:
    """`a - b` where either side may be missing, yielding NaN instead of raising. Score keys accumulate over time (mean_best_iter, 2026-07-28), so a render that spans the change has entries whose key sets differ; a missing metric should leave a blank cell, not make the whole historical run unrenderable -- the same principle _resolve_cols applies to retired names."""
    return float("nan") if a is None or b is None else a - b


def _resolve_cols(score: dict, rank_by: str, cols: list[str]) -> tuple[str, list[str]]:
    """Map configured column names onto whatever schema THIS run actually logged.

    A current run has them verbatim. A pre-rework run gets its _LEGACY_ALIAS equivalent. Anything present in neither is dropped rather than raising, so one retired metric cannot make a whole historical run unrenderable.
    """

    def pick(m):
        if m in score:
            return m
        alt = _LEGACY_ALIAS.get(m)
        return alt if alt in score else None

    resolved = [c for c in (pick(m) for m in cols) if c is not None]
    rank = pick(rank_by)
    if rank is None:
        rank = resolved[0] if resolved else rank_by
        # LOUD, because the silent version was a live bug: CLF logs stopped carrying lofo_prauc after the metric rework, the drop-one path never resolved at all, every delta came back NaN, every sort key collapsed to the same sentinel and the stable sort left the rows in log order -- under a "Rank" column asserting otherwise. A fallback is readable history; an unannounced one is a wrong answer.
        if rank_by not in _WARNED:
            _WARNED.add(rank_by)
            print(f"  [warn] {rank_by!r} absent from this run's scores -- ranking on {rank!r} instead")
    return rank, resolved


_WARNED: set[str] = set()  # one fallback warning per metric name per process, not per table


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


def _added_importance(entry: dict, base_feats: set, key: str) -> float:
    """Sum the added feature's importance across whatever columns it introduced -- i.e. this recipe's feature columns that aren't in the base. Handles bucketed families, multi-column harmonics, and single columns uniformly (added = recipe features - base features)."""
    imp = entry.get(key, {}) or {}
    added = [c for c in imp if c not in base_feats]
    return float(sum(imp[c] for c in added)) if added else float("nan")


def _sum_over(imp: dict, cols: list) -> float:
    """Sum an importance dict over the given columns (NaN if none are present)."""
    vals = [imp[c] for c in cols if c in imp]
    return float(sum(vals)) if vals else float("nan")


def _captioned(heading: str, caption: str, table: str) -> str:
    """A '### heading', a one-sentence italic caption, then the table."""
    return f"### {heading}\n\n_{caption}_\n\n{table}"


def _fold(df: pd.DataFrame, index_label: str, keep: int = _TAIL) -> str:
    """Markdown table with the middle rows folded into a <details> block.

    A screen of ~70 candidates renders ~70 identically-weighted rows of which the interesting ones are at the two ends; the middle is by construction the band where nothing cleared the noise floor. Folded, not dropped -- the rows are still there for anyone who wants them.
    """
    if len(df) <= 2 * keep + 4:
        return _table(df, index_label)
    head, mid, tail = df.iloc[:keep], df.iloc[keep:-keep], df.iloc[-keep:]
    return (
        _table(head, index_label)
        + f"\n\n<details><summary>{len(mid)} further rows (middle of the ranking)</summary>\n\n"
        + _table(mid, index_label)
        + "\n\n</details>\n\n"
        + _table(tail, index_label)
    )


def _mark(v, floor: float | None) -> str:
    """A delta cell, bolded when it clears the noise floor -- so magnitude is visible at a glance rather than requiring the reader to compare every row against a number in the prose.

    `floor=None` means do not bold at all, which is the right answer for every column but the headline: the companion metrics are on their own scales (lofo_prauc_lift is average precision over the base rate, so its deltas run several times the headline's) and the floor was measured for the headline only. Bolding them against it would mark noise as signal on three columns out of four.
    """
    if v is None or v != v:
        return "NaN"
    return f"**{v:+.4f}**" if floor is not None and abs(v) >= floor else f"{v:+.4f}"


def _unit_of(entry: dict, name: str) -> str:
    """The feature unit a recipe name refers to. Prefers the logged `unit` field; falls back to stripping the add_/drop_ prefix for entries logged before it existed."""
    if entry.get("unit"):
        return entry["unit"]
    for p in ("add_", "drop_"):
        if name.startswith(p):
            return name[len(p) :]
    return name


def _gap_row(unit: str, add_d: float, drop_d: float, shipped, n_cols, floor: float) -> tuple[str, str]:
    """(flag, why) for one unit, from its two deltas against the noise floor.

    The manifest's job is to test feature usefulness INDEPENDENTLY of what currently ships, so the shipped recipe is a label here and never a filter. That makes four actionable quadrants -- two for things the recipe is missing, two for things it carries and shouldn't -- plus an explicit unmeasurable bucket. A unit too small to move the headline is reported as UNMEASURABLE, never as inert: a 1-column change out of ~100 cannot clear the floor in either direction, so calling it "doesn't help" would be reading noise as a result.
    """
    small = n_cols is not None and n_cols <= 1
    if (add_d != add_d) and (drop_d != drop_d):
        return "", ""
    if small and abs(add_d if add_d == add_d else 0) < floor and abs(drop_d if drop_d == drop_d else 0) < floor:
        return "UNMEASURABLE", f"{n_cols} column, below the {floor:+.4f} floor either way"
    if shipped is False:
        if drop_d == drop_d and drop_d <= -floor:
            return "OMITTED-SYNERGY", "not shipped, yet the full model loses skill without it"
        if add_d == add_d and add_d >= floor:
            return "OMITTED-USEFUL", "not shipped, yet it earns its place added alone"
    if shipped is True:
        if drop_d == drop_d and drop_d >= floor:
            return "SHIPPED-HARMFUL", "shipped, yet the full model is BETTER without it"
        if drop_d == drop_d and abs(drop_d) < floor:
            return "SHIPPED-INERT", "shipped, but removing it costs nothing measurable"
    return "", ""


def _gap_section(task: str, add_by_name: dict, drop_by_name: dict) -> str:
    """The actionable output: where the shipped recipe and the measured evidence disagree.

    Replaces the old synergy cross-tab, which classified every unit but said nothing about what ships. Same two deltas, read against `shipped` and the task's measured noise floor.
    """
    base, full = add_by_name.get("base"), drop_by_name.get("full_feature_set")
    if base is None or full is None:
        return _captioned("Shipped-recipe gap", "requires both an add-one base and a drop-one full model.", "_(missing base or full)_")
    rank, _ = _resolve_cols(base["score"], _RANK_METRIC[task], _SCOREBOARD[task]["cols"])
    floor = _FLOOR[task]
    rows, unmeasurable = {}, []
    seen = {n[len("add_"):] for n in add_by_name if n.startswith("add_")} | {
        n[len("drop_"):] for n in drop_by_name if n.startswith("drop_")
    }
    for f in sorted(seen):
        a_e, d_e = add_by_name.get(f"add_{f}"), drop_by_name.get(f"drop_{f}")
        a = _sub(a_e["score"].get(rank), base["score"].get(rank)) if a_e else float("nan")
        d = _sub(d_e["score"].get(rank), full["score"].get(rank)) if d_e else float("nan")
        meta = a_e or d_e or {}
        shipped, n_cols = meta.get("shipped"), meta.get("n_cols")
        flag, why = _gap_row(f, a, d, shipped, n_cols, floor)
        if flag == "UNMEASURABLE":
            unmeasurable.append(f)
            continue
        if flag:
            rows[f] = {
                "flag": flag,
                "shipped": "yes" if shipped else "no" if shipped is False else "?",
                "n_cols": n_cols,
                # A base block has no add_ arm by construction -- it is always present, so there is nothing to add it TO. "n/a" rather than NaN, which reads as a failed measurement instead of one that was never applicable; its evidence is the drop column alone.
                "add Δ": _mark(a, floor) if a == a else ("n/a" if a_e is None else "NaN"),
                "drop Δ": _mark(d, floor) if d == d else ("n/a" if d_e is None else "NaN"),
                "why": why,
            }
    if not rows:
        body = "_(nothing clears the noise floor in either direction — the shipped recipe and the evidence agree)_"
    else:
        order = ["OMITTED-SYNERGY", "OMITTED-USEFUL", "SHIPPED-HARMFUL", "SHIPPED-INERT"]
        ordered = sorted(rows.items(), key=lambda kv: (order.index(kv[1]["flag"]), kv[0]))
        body = _table(pd.DataFrame({f: v for f, v in ordered}).T, "Feature")
    cap = (
        f"Where the SHIPPED recipe and this run's evidence disagree, on {rank}, at the measured single-run "
        f"floor of ±{floor:.4f}. OMITTED-* = the recipe is missing something that pays; SHIPPED-* = it carries "
        f"something that doesn't. This is the manifest's actual product — everything below is the evidence."
    )
    foot = ""
    if unmeasurable:
        foot = (
            f"\n\n_Too small to measure ({len(unmeasurable)}): {', '.join(unmeasurable)} — single-column units, "
            f"where a true effect and no effect are indistinguishable at this cohort size. Not evidence of inertness._"
        )
    return _captioned("Shipped-recipe gap", cap, body) + foot


def _reference_scores(task: str, base: dict | None, full: dict | None) -> str:
    """The only RAW scores in the report: the two reference models everything else is a delta against.

    A raw row per candidate used to be printed too -- 26% of the file, and pure duplication of the delta tables plus one base row, padded with n_sites/n_families/n_rows columns identical on every row.
    """
    have = [(n, e) for n, e in (("base", base), ("full_feature_set", full)) if e is not None]
    if not have:
        return ""
    metrics = [m for m in have[0][1]["score"] if m not in _COUNT_KEYS or m == "n_feat"]
    df = pd.DataFrame({n: {m: e["score"].get(m) for m in metrics} for n, e in have}).T[metrics]
    return _captioned(
        "Reference scores",
        "The two models every delta below is measured against — the base recipe and the full feature set. Raw, absolute; every other table is a difference from one of these.",
        _table(df, "recipe"),
    )


def _add_one_table(task: str, entries: list[dict]) -> str:
    """The add-one screen, as ONE table: deltas + the candidate's own importance + its size.

    Was four tables of the same ~70 rows -- a scoreboard, raw scores, deltas and importances -- in three DIFFERENT orders, because the scoreboard sorted on a delta while the rest sorted on the raw score. One table, one order, sorted on the headline delta.
    """
    by_name = {e["name"]: e for e in entries}
    if "base" not in by_name:
        return "_(add-one: no base recipe logged)_"
    base, full = by_name["base"], by_name.get("full_feature_set")
    rank, cols = _resolve_cols(base["score"], _RANK_METRIC[task], _SCOREBOARD[task]["cols"])
    floor = _FLOOR[task]
    base_feats = set(base["features"])
    full_gain = (full or {}).get("importance", {}) or {}
    adds = [e for e in entries if e["name"] not in ("base", "full_feature_set")]

    def d(e, m):
        return _sub(e["score"].get(m), base["score"].get(m))

    adds.sort(key=lambda e: (v if (v := d(e, rank)) == v else -1e9), reverse=True)
    rows = {}
    for i, e in enumerate(adds, 1):
        added = [c for c in e["features"] if c not in base_feats]
        rows[_unit_of(e, e["name"])] = {
            "#": i,
            "ship": "yes" if e.get("shipped") else "no" if e.get("shipped") is False else "?",
            "n_cols": e.get("n_cols"),
            **{f"{m} Δ": _mark(d(e, m), floor if m == rank else None) for m in cols},
            "gain": _added_importance(e, base_feats, "importance"),
            "perm": _added_importance(e, base_feats, "importance_perm"),
            "full_gain": _sum_over(full_gain, added),
        }
    df = pd.DataFrame(rows).T
    return _captioned(
        f"Add-one screen (Δ vs base, ranked by {rank})",
        f"Each candidate added ALONE over the base. `gain`/`perm` are its importance in its own add-one model; "
        f"`full_gain` in the all-features model. Bold = clears the ±{floor:.4f} noise floor on {rank}; "
        f"anything unbolded is not a result.",
        _fold(df, "Feature"),
    )


def _drop_one_table(task: str, entries: list[dict]) -> str:
    """The drop-one leave-one-out, as ONE table.

    `rank` is RESOLVED against the schema this run logged. Unresolved, it was the report's worst bug: CLF logs lost lofo_prauc in the metric rework, so every delta came back NaN, every sort key collapsed to the same sentinel, the stable sort left the rows in raw log order, and a "Rank" column asserted a ranking that did not exist.
    """
    by_name = {e["name"]: e for e in entries}
    full = by_name.get("full_feature_set")
    if full is None:
        return "_(drop-one: no full_feature_set logged)_"
    drops = [e for e in entries if e["name"].startswith("drop_")]
    full_score, full_feats = full["score"], set(full["features"])
    rank, cols = _resolve_cols(full_score, _RANK_METRIC[task], _SCOREBOARD[task]["cols"])
    floor = _FLOOR[task]
    full_gain = full.get("importance", {}) or {}

    def d(e, m):
        return _sub(e["score"].get(m), full_score.get(m))

    drops.sort(key=lambda e: (v if (v := d(e, rank)) == v else 1e9))  # most load-bearing (most negative) first
    rows = {}
    for i, e in enumerate(drops, 1):
        dropped = [c for c in full_feats if c not in set(e["features"])]
        rows[_unit_of(e, e["name"])] = {
            "#": i,
            "ship": "yes" if e.get("shipped") else "no" if e.get("shipped") is False else "?",
            "n_cols": e.get("n_cols") or len(dropped),
            **{f"{m} Δ": _mark(d(e, m), floor if m == rank else None) for m in cols},
            "full_gain": _sum_over(full_gain, dropped),
        }
    return _captioned(
        f"Drop-one leave-one-out (Δ vs full, ranked by {rank})",
        f"LOFO loss when each unit is removed from the full model; most load-bearing (most negative) first. "
        f"Bold = clears the ±{floor:.4f} noise floor on {rank}. A unit of 1-2 columns cannot clear it either "
        f"way — read those as unmeasurable, not inert.",
        _fold(pd.DataFrame(rows).T, "Dropped unit"),
    )


def _geometry_line(entries: list[dict]) -> str:
    """The feature-construction geometry this task's entries were built with.

    Reported per TASK, not per run, because REG and CLF ship different geometries. It is load-bearing for cross-run reading: changing the bucket RADII while keeping the bucket COUNT leaves n_feat identical, so without this line two runs can differ in edges/lam with nothing in the report to show it. Legacy entries predate the field and say so rather than implying a default.
    """
    geoms = {json.dumps(e.get("geometry"), sort_keys=True) for e in entries}
    if geoms == {"null"}:
        return "_Geometry: not recorded (pre-2026-07-28 run) — not comparable to a run with a different edges/lam._"
    parts = []
    for g in sorted(geoms):
        d = json.loads(g)
        parts.append("not recorded" if d is None else f"edges={tuple(d['edges'])} · vel={d['vel']} · lam={d['lam']:,}")
    joined = " ; ".join(parts)
    warn = "  ⚠ **mixed geometries in one task** — these rows are not comparable" if len(parts) > 1 else ""
    return f"_Geometry: {joined}._{warn}"


def _config_line(entries: list[dict]) -> str:
    """Which XGBoost config each mode was fitted at.

    Load-bearing since the add-one and drop-one arms run at DIFFERENT configs -- SCREEN for base-sized arms, FULL for full-sized ones. Within a mode every arm shares one, so its deltas stay paired; across modes they do not, so an add-one score must never be read against a drop-one score. The log has carried `xgb` all along and nothing rendered it.
    """
    seen = {}
    for e in entries:
        cfg = e.get("xgb")
        if cfg:
            keys = ("n_estimators", "max_depth", "learning_rate", "reg_lambda", "colsample_bytree")
            seen.setdefault(e.get("mode", "?"), ", ".join(f"{k}={cfg[k]}" for k in keys if k in cfg))
    if not seen:
        return "_Config: not recorded._"
    return "\n".join(f"_Config ({m}): {v}._" for m, v in sorted(seen.items()))


def _run_task_section(task: str, entries: list[dict]) -> str:
    """One task's block: the shipped-recipe gap first (the actionable part), then the evidence."""
    te = [e for e in entries if e["task"] == task]
    add = {e["name"]: e for e in te if e.get("mode") == "add_one"}
    drop = {e["name"]: e for e in te if e.get("mode") == "drop_one"}
    parts = [f"## {task.upper()}", _geometry_line(te), _config_line(te)]
    if add and drop:
        parts.append(_gap_section(task, add, drop))
    parts.append(_reference_scores(task, add.get("base"), drop.get("full_feature_set") or add.get("full_feature_set")))
    if add:
        parts.append(_add_one_table(task, list(add.values())))
    if drop:
        parts.append(_drop_one_table(task, list(drop.values())))
    return "\n\n".join(p for p in parts if p)


def _run_index(log: dict) -> dict:
    """{run_id -> {tasks, modes, variants, n, ts}} summarising each run in the log.

    `variants` is in the listing rather than only in the rendered report because that listing is where a run gets CHOSEN. An island and a network run are not comparable (see _variant_note), and picking between them off a line that showed only tasks/modes/count meant opening both reports to find out which was which.
    """
    from collections import defaultdict

    info = defaultdict(lambda: {"tasks": set(), "modes": set(), "variants": set(), "n": 0, "ts": ""})
    for e in log.values():
        d = info[e.get("run_id", "(legacy)")]
        d["tasks"].add(e["task"])
        d["modes"].add(e.get("mode", "?"))
        d["variants"].add(e.get("variant") or "unrecorded")
        d["n"] += 1
        d["ts"] = max(d["ts"], e.get("timestamp", ""))
    return info


def _variant_note(entries: list[dict]) -> str:
    """The run's island/network line for the report header.

    An ABSENT field is not island, for the same reason it is not False for true_lofo: every pre-flag run WAS island, but the log does not say so and the header must not assert it as recorded fact. A run mixing both is only reachable by hand-editing the log -- run_exp takes one required, mutually-exclusive variant per invocation -- but it is the one case where the tables below would be silently meaningless, so it is called out rather than assumed away.
    """
    seen = {e.get("variant") for e in entries}
    if seen == {"island"}:
        return "**Variant = island** — the donor-free feature set; no reach-graph neighbour columns."
    if seen == {"network"}:
        return (
            "**Variant = network** — the base carries the reach-graph donor block. NOT comparable to an island run: "
            "the donor block is defined only where a neighbour resolves (~88% of sites; the rest get the columns "
            "all-NaN), and reach neighbours co-fall with the basin families LOFO holds out, so a held-out site's "
            "donor is usually held out with it. Read the `drop_nbr_*` rows for what the block is worth."
        )
    if seen == {None}:
        return "**Variant = not recorded** — this run predates the flag; it was island, but nothing in the log says so."
    return "**Variant = ⚠ MIXED** — this run's entries disagree on island vs network; its rows are not comparable to each other."


def render(run_id: str | None = None, log_path: Path | None = None):
    """Render one run to results_{run_id}.md. `log_path` mirrors run_exp's --log, so a shakedown written to a scratch log can be rendered without its run_id landing in the tracked one."""
    log_path = log_path or _LOG
    if not log_path.exists() or log_path.stat().st_size == 0:
        raise SystemExit(f"no log at {log_path} -- run run_exp.py first")
    log = json.loads(log_path.read_text())
    info = _run_index(log)
    if run_id is None:  # require an id; with none, list valid ids
        print("valid run_ids (newest first) -- render with:  python render_results.py <run_id>")
        for rid in sorted(info, reverse=True):
            d = info[rid]
            print(
                f"  {rid:<26} variant={'/'.join(sorted(d['variants'])):<11} tasks={sorted(d['tasks'])} "
                f"modes={sorted(d['modes'])} entries={d['n']} last={d['ts']}"
            )
        return
    if run_id == "latest":
        run_id = max(info)
    if run_id not in info:
        raise SystemExit(f"unknown run_id {run_id!r}. Valid: {sorted(info, reverse=True)}")
    entries = [e for e in log.values() if e.get("run_id") == run_id]
    tasks = sorted({e["task"] for e in entries})
    # Which holdout produced the lofo_* columns. The true leave-one-family-out regime returns the
    # IDENTICAL column set as the GroupKFold one, so nothing in the numbers themselves distinguishes them -- absent from the log means a pre-flag run, which was always GroupKFold.
    # An ABSENT flag is not False. Defaulting it made the header assert "GroupKFold LOFO, the default regime"
    # as fact about a run that recorded nothing either way.
    flags = {e.get("true_lofo") for e in entries}
    true_lofo = (
        "T" if flags == {True} else "F" if flags == {False} else "not recorded" if flags == {None} else "MIXED"
    )
    note = {
        "T": "  — one fold per basin family, held out whole; NOT comparable to a `True Lofo = F` run.",
        "F": "  — GroupKFold LOFO (~1/5 of families per fold), the default regime.",
        "not recorded": "  — this run predates the flag; it was GroupKFold, but nothing in the log says so.",
    }.get(true_lofo, "  — ⚠ this run mixes both holdout regimes; its rows are not comparable to each other.")
    floors = ", ".join(f"{t.upper()} ±{_FLOOR[t]:.4f}" for t in sorted(_FLOOR))
    header = (
        f"# Feature-manifest report — run `{run_id}`\n\n"
        f"{_variant_note(entries)}\n\n"
        f"**True Lofo = {true_lofo}**{note}\n\n"
        "Per task: the **shipped-recipe gap** — where the recipe and the evidence disagree — then the "
        "reference scores and the **add-one** / **drop-one** evidence behind it. Judge on LOFO; a delta "
        f"inside the measured single-run noise floor ({floors}) is not a result, and is left unbolded.\n\n"
    )
    out = log_path.parent / f"results_{run_id}.md"
    out.write_text(header + "\n---\n\n".join(_run_task_section(t, entries) for t in tasks) + "\n")
    print(f"wrote {out}  (run {run_id}, tasks {tasks}, {len(entries)} entries)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?", default=None, help="run id to render (or 'latest'); omit to list valid ids")
    ap.add_argument("--log", type=Path, default=None, help="read this log instead of log.json (mirrors run_exp --log)")
    a = ap.parse_args()
    render(a.run_id, a.log)

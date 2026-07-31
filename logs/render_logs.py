"""Render logs/fulltrain_logs.json -> human-readable markdown.

Run:  python logs/render_logs.py      (paths resolve relative to this file, so any cwd works)

Writes three files next to the logs:
  scores.md       -- one headline block per model run: recipe, feature list, score table
  importances.md  -- one block per run: gain + permutation importance table (the heavier data)
  experiments.md  -- latest run per experiment, from the separate experiments.jsonl

fulltrain_logs.json is a dict keyed by sequential integer (see src/models/train.log_metadata); each entry has name / recipe / features / target_col / task / xgb / true_lofo / score / importance / importance_perm.

experiments.jsonl is one JSON record per line, appended by experiments/specific-small-experiments/run_exp.py (Question -> Winner -> Results). This module owns that rendering: run_exp.py imports format_run/render_experiments from here rather than keeping a second copy, so the two can never drift. Rendering it is optional -- a missing experiments.jsonl is skipped, not an error.
"""

import json
import pandas as pd
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LOGFILE = _HERE / "fulltrain_logs.json"
_SCORES_MD = _HERE / "scores.md"
_IMPORTANCES_MD = _HERE / "importances.md"
_EXPERIMENTS_JSONL = _HERE / "experiments.jsonl"  # append-only experiment history (run_exp.py)
_EXPERIMENTS_MD = _HERE / "experiments.md"


def _model_type(entry):
    """'reg'/'clf' task -> 'REG'/'CLF' (falls back to the upper-cased task string)."""
    return {"reg": "REG", "clf": "CLF"}.get(entry.get("task", ""), str(entry.get("task", "?")).upper())


def _fmt(v):
    """Compact table cell: ints as-is, floats to 4dp (NaN -> 'NaN'), else str()."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:  # NaN
            return "NaN"
        if v == int(v) and abs(v) < 1e15:  # count-like (pandas upcasts n_sites/n_rows to float)
            return str(int(v))
        return f"{v:.4f}"
    return str(v)


def _make_markdown_table(df, index_label=None):
    """Render `df` as a markdown table, cells passed through `_fmt`. With `index_label`, the row index is emitted as the leading column under that header (the transposed importance table uses it for the Gain/Perm row labels)."""
    header = ([index_label] if index_label is not None else []) + [str(c) for c in df.columns]
    lines = [header, ["---"] * len(header)]
    for idx, row in df.iterrows():
        lines.append(([str(idx)] if index_label is not None else []) + [_fmt(v) for v in row])
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)


def _score_table(score):
    # transposed: metric names across the header, a single row of values
    n = 8
    items = list(score.items())
    scores = [dict(items[i : i + n]) for i in range(0, len(items), n)]
    lines = [_make_markdown_table(pd.DataFrame([score])) for score in scores]
    table = "\n\n".join(lines)
    return table


def beta_table_md(entry):
    """The decision-threshold table for a classifier run, as markdown ('' if absent).

    Reads the `beta_table` train.py logs from the CV's LOFO out-of-fold vector. Columns beyond recall/precision/fdr are DERIVED here rather than stored, because they follow exactly from (base_rate p, recall r, precision pi): TP = r*p, FP = TP*(1-pi)/pi, FN = p - TP, TN = (1-p) - FP, so accuracy = TP+TN and FPR = FP/(1-p).

    Accuracy is shown with the do-nothing baseline beside it: at a ~26% base rate "never alarm" scores ~74%, so every operating point above beta 1 is WORSE than that and reads as a broken model unless the baseline is stated. Regression runs and entries logged before the field existed both yield ''.
    """
    table = entry.get("beta_table")
    p = entry.get("base_rate")
    if not table or p is None:
        return ""

    rows = []
    for r in table:
        rec, pi = r["recall"], r["precision"]
        tp = rec * p
        fp = tp * (1 - pi) / pi if pi > 0 else float("nan")
        rows.append(
            {
                # pre-formatted: _fmt renders int-valued floats as ints, which would print the grid as "0.5, 1, 1.5, 2" -- ragged for a column whose values are all half-steps
                "beta": f"{r['beta']:.1f}",
                "tau": r["tau"],
                "recall": rec,
                "fdr": r["fdr"],
                "precision": pi,
                "accuracy": tp + ((1 - p) - fp),
                "fpr": fp / (1 - p) if p < 1 else float("nan"),
                "lift": pi / p if p > 0 else float("nan"),
            }
        )

    note = (
        f"Base rate {p:.4f} (in the scored rows) — 'never alarm' is {1 - p:.1%} accurate. "
        f"Lift is precision ÷ base rate at that point."
    )
    cover = entry.get("beta_table_coverage")
    if cover is not None and cover < 0.999:
        note += f" **Scored on {cover:.1%} of pooled rows** — a capped true-LOFO run, so this describes only the eligible families."
    return f"\n### Decision thresholds:\n\n{note}\n\n{_make_markdown_table(pd.DataFrame(rows))}\n"


def _scores_block(key, entry):
    num_feats = len(entry.get("features", []))
    feats = ", ".join(entry.get("features", [])) or "_(none logged)_"
    return (
        f"# {_model_type(entry)} Model {key} \n\n"
        f"| | |\n"
        f"|-|-|\n"
        f"| **Timestamp:** | [{entry.get("timestamp", "?")}] | \n"
        f"|**Recipe:** | {entry.get('recipe', '?')}  |\n"  # trailing 2 spaces -> markdown hard line break
        f"|**Name:** | {entry.get('name', '?')}  |\n"  # trailing 2 spaces -> markdown hard line break
        f"|**Notes:** | {entry.get('notes', '<no notes>')}  |\n"  # trailing 2 spaces -> markdown hard line break
        # Which family holdout produced the lofo_* scores. True LOFO returns the IDENTICAL column
        # set as the default GroupKFold one but a systematically higher number, so two runs are only comparable when this matches. Absent on pre-flag entries, which were all GroupKFold.
        f"|**True Lofo:** | {'T' if entry.get('true_lofo', False) else 'F'}|\n"
        f"|**# Features:** | {num_feats}|\n"
        f"| **List of Features:** | {feats}  | \n\n"
        f"### Scores:\n"
        f"{_score_table(entry.get('score', {}))}\n"
        f"{beta_table_md(entry)}"
    )


def _importance_table(entry):
    # access the gain of the entry dict safely falls back to {} when importance key exists but is None
    gain = entry.get("importance", {}) or {}
    perm = entry.get("importance_perm", {}) or {}
    if not gain and not perm:
        return "_(no importances logged)_"

    feats = list(gain) + [f for f in perm if f not in gain]  # gain is already gain-ranked
    d = {
        "gain": [gain[f] if f in gain else "-" for f in feats],
        "perm": [perm[f] if f in perm else "-" for f in feats],
    }
    # transposed: features across the header, a Gain row and a Perm row (missing -> em dash)
    df = pd.DataFrame(d, index=feats).sort_values(by="perm", ascending=False)

    return _make_markdown_table(df, index_label="features")


def _importances_block(key, entry):
    return (
        f"# {_model_type(entry)} Model {key}\n\n"
        f"Recipe: {entry.get('recipe', '?')}\n"
        f"Date: {entry.get('timestamp', '?')}\n"
        f"{_importance_table(entry)}\n"
    )


# ── experiments.jsonl -> experiments.md ───────────────────────────────────────
# The SINGLE implementation of this rendering. experiments/specific-small-experiments/run_exp.py refreshes experiments.md after every run and imports these rather than keeping its own copy -- two renderers for one file drift, and the drift only shows up as a confusing diff much later. `_exp_fmt` is deliberately NOT the `_fmt` above: the experiment tables have always used 4 significant figures, while the training scores use 4 decimal places, and unifying them here would silently rewrite every row of experiments.md.


def _exp_fmt(v) -> str:
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def load_experiments() -> list[dict]:
    """The append-only experiment history, oldest first. Missing file -> []."""
    if not _EXPERIMENTS_JSONL.exists():
        return []
    return [json.loads(line) for line in _EXPERIMENTS_JSONL.read_text().splitlines() if line.strip()]


def latest_per_exp(records) -> dict:
    """Last record per (exp, task) -- jsonl append order is chronological."""
    latest = {}
    for r in records:
        latest[f"{r['exp']}[{r['task']}]"] = r
    return latest


def format_run(r: dict, md: bool = False) -> str:
    """Question -> Winner -> Results, per the required layout."""
    h = r["headline_metric"]
    metrics = r["metrics"]
    w = r["winner"]
    delta = r["delta_vs_base"].get(w["recipe"], {}).get(h)
    dtxt = f", {delta:+.4f} vs {r['baseline']}" if delta is not None and w["recipe"] != r["baseline"] else ""
    order = sorted(metrics, key=lambda x: (metrics[x].get(h) is not None, metrics[x].get(h)), reverse=True)
    cols = list(metrics[order[0]].keys()) if order else []

    L = []
    if md:
        L.append(f"### {r['exp']} [{r['task']}]")
        L.append(f"**Question.** {r['question']}")
        L.append("")
        L.append(f"**Winner.** `{w['recipe']}` — {h} = {_exp_fmt(w['value'])}{dtxt}")
        L.append(f"<sub>{r['ts']} · {r['mode']} · {r['n_sites']} sites · git {r.get('git_sha') or '?'}</sub>")
        L.append("")
        L.append("**Results.**")
        L.append("")
        L.append("| recipe | " + " | ".join(cols) + " |")
        L.append("|" + "---|" * (len(cols) + 1))
        for rp in order:
            star = " ⭐" if rp == w["recipe"] else ""
            L.append(f"| {rp}{star} | " + " | ".join(_exp_fmt(metrics[rp].get(c, "")) for c in cols) + " |")
        top = r.get("top_gain", {}).get(w["recipe"])
        if top:
            L.append("")
            L.append(f"_Top features (winner):_ {', '.join(top)}")
    else:
        L.append(f"Question. {r['question']}")
        L.append(f"  Winner.  {w['recipe']} ({h}={_exp_fmt(w['value'])}{dtxt})")
        L.append(f"  Results ({r['mode']}, {r['n_sites']} sites, git {r.get('git_sha') or '?'}):")
        for rp in order:
            star = "*" if rp == w["recipe"] else " "
            L.append(f"   {star} {rp:<24.24s} {h}={_exp_fmt(metrics[rp].get(h))}")
    return "\n".join(L)


def render_experiments(quiet: bool = False) -> int:
    """experiments.jsonl -> experiments.md (latest run per experiment). Returns the block count."""
    records = latest_per_exp(load_experiments())
    out = ["# Experiment log", "", "_Latest run per experiment. Full append-only history: logs/experiments.jsonl_", ""]
    for key in records:
        out.append(format_run(records[key], md=True))
        out.append("")
    _EXPERIMENTS_MD.parent.mkdir(parents=True, exist_ok=True)
    _EXPERIMENTS_MD.write_text("\n".join(out))
    if not quiet:
        print(f"wrote {_EXPERIMENTS_MD}  ({len(records)} experiment-runs)")
    return len(records)


def render():
    """Render every log this directory owns. The training log is required; the experiment log is optional (it only exists once an experiment has run), so a missing experiments.jsonl is a skip, not an error."""
    if not _LOGFILE.exists() or _LOGFILE.stat().st_size == 0:
        raise SystemExit(f"no training log at {_LOGFILE} -- run a training build first")
    with open(_LOGFILE) as f:
        log = json.load(f)
    keys = sorted(log, key=int, reverse=True)  # integer order despite JSON's string keys

    _SCORES_MD.write_text("\n---\n\n".join(_scores_block(k, log[k]) for k in keys) + "\n")
    _IMPORTANCES_MD.write_text("\n---\n\n".join(_importances_block(k, log[k]) for k in keys) + "\n")
    print(f"wrote {_SCORES_MD}  ({len(keys)} models)")
    print(f"wrote {_IMPORTANCES_MD}  ({len(keys)} models)")

    if _EXPERIMENTS_JSONL.exists():
        render_experiments()
    else:
        print(f"skipped {_EXPERIMENTS_MD.name}  (no {_EXPERIMENTS_JSONL.name} yet)")


if __name__ == "__main__":
    render()

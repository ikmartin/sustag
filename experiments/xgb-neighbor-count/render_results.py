"""render_results.py -- one run of the neighbour-count sweep -> results_<run_id>.md.

    python render_results.py <run_id>     # or `latest`; no arg lists valid ids

Deltas are against the ISLAND arm, which is the paired baseline every other arm is the island columns plus a donor block. A delta inside the measured single-run noise floor is not a result and is left unbolded -- the floors are the feature-manifest ones (REG 0.0085 lofo_r2, CLF 0.0037 lofo_prauc), which were measured on the same cohort and the same fold machinery.

The grid is also folded into a COUNT x INDICATOR table per rule, because "does a 4th donor help" is a question about a column of that table rather than about any single arm's rank.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_LOG = _HERE / "log.json"
_HEAD = {"reg": "lofo_r2", "clf": "lofo_prauc"}
_FLOOR = {"reg": 0.0085, "clf": 0.0037}
_ALSO = {
    "reg": ["lofo_between_r2", "lofo_within_r2", "lofo_rmse", "loso_r2"],
    "clf": ["lofo_auc", "lofo_prauc_lift", "lofo_between_rate_r2", "loso_auc"],
}


def _fmt(v, digits=4):
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:.{digits}f}"


def _rows(entries):
    out = []
    for e in entries:
        s = e.get("score", {})
        out.append({"name": e["name"], "n_feat": s.get("n_feat"), **s})
    return pd.DataFrame(out)


def _scoreboard(task, df, base_name):
    head, floor = _HEAD[task], _FLOOR[task]
    base = df.loc[df.name == base_name, head]
    b = float(base.iloc[0]) if len(base) else float("nan")
    df = df.sort_values(head, ascending=False)
    cols = [head] + [c for c in _ALSO[task] if c in df.columns]
    lines = [f"| arm | n_feat | Δ vs island | " + " | ".join(cols) + " |",
             "|---|---|---|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        d = r[head] - b
        star = " ⭐" if r["name"] == df.iloc[0]["name"] else ""
        dtxt = "—" if r["name"] == base_name else (f"**{d:+.4f}**" if abs(d) >= floor else f"{d:+.4f}")
        lines.append(f"| {r['name']}{star} | {int(r['n_feat'])} | {dtxt} | " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def _grid(task, df, rule):
    """COUNT x INDICATOR for one similarity rule -- the table the experiment exists to read."""
    head = _HEAD[task]
    sub = df[df.name.str.endswith(f"_{rule}") & df.name.str.startswith("nearest_")]
    if sub.empty:
        return ""
    lines = [f"**{rule} ranking**", "", "| n donors | sign | flag | rednt |", "|---|---|---|---|"]
    for n in sorted({int(x.split("_")[1]) for x in sub.name}):
        cells = []
        for ind in ("sign", "flag", "rednt"):
            m = sub[sub.name == f"nearest_{n}_nbr_{ind}_{rule}"]
            cells.append(_fmt(float(m[head].iloc[0]) if len(m) else None))
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render(run_id=None, log_path=None):
    log_path = log_path or _LOG
    if not log_path.exists() or log_path.stat().st_size == 0:
        raise SystemExit(f"no log at {log_path} -- run run_exp.py first")
    log = json.loads(log_path.read_text())
    runs = {}
    for e in log.values():
        d = runs.setdefault(e.get("run_id", "(legacy)"), {"tasks": set(), "n": 0, "ts": ""})
        d["tasks"].add(e["task"])
        d["n"] += 1
        d["ts"] = max(d["ts"], e.get("timestamp", ""))
    if run_id is None:
        print("valid run_ids (newest first) -- render with:  python render_results.py <run_id>")
        for rid in sorted(runs, reverse=True):
            d = runs[rid]
            print(f"  {rid:<26} tasks={sorted(d['tasks'])} arms={d['n']} last={d['ts']}")
        return
    if run_id == "latest":
        run_id = max(runs)
    if run_id not in runs:
        raise SystemExit(f"unknown run_id {run_id!r}. Valid: {sorted(runs, reverse=True)}")

    entries = [e for e in log.values() if e.get("run_id") == run_id]
    parts = [
        f"# Neighbour-count sweep — run `{run_id}`\n",
        f"Donor **count** × **indicator** encoding × **similarity** rule, all arms of a task fit at one shared config so every "
        f"delta isolates the feature set. Deltas are against the island arm; anything inside the noise floor "
        f"(REG ±{_FLOOR['reg']}, CLF ±{_FLOOR['clf']}) is left unbolded and is not a result.\n",
        f"_Coverage caveat: the high slots are sparse by construction — ≥1 donor 87.9% of sites, ≥2 74.1%, ≥3 59.5%, ≥4 44.0%, "
        f"and 14 sites have none at any depth. Split `nearest_4` by `num_neighbors` in eval_<task>.csv before reading it._\n",
    ]
    for task in sorted({e["task"] for e in entries}):
        te = [e for e in entries if e["task"] == task]
        df = _rows(te)
        base_name = f"island_{task.upper()}"
        cfg = te[0].get("xgb", {})
        keys = ("n_estimators", "max_depth", "learning_rate", "reg_lambda", "min_child_weight", "colsample_bytree")
        parts.append(f"## {task.upper()}\n")
        parts.append("_Config (shared by every arm): " + ", ".join(f"{k}={cfg[k]}" for k in keys if k in cfg) + "._\n")
        parts.append(_scoreboard(task, df, base_name))
        for rule in ("area", "dist"):
            g = _grid(task, df, rule)
            if g:
                parts.append("\n" + g)
    out = log_path.parent / f"results_{run_id}.md"
    out.write_text("\n".join(parts) + "\n")
    print(f"wrote {out}  (run {run_id}, {len(entries)} arms)")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?", default=None)
    ap.add_argument("--log", type=Path, default=None)
    a = ap.parse_args()
    render(a.run_id, a.log)

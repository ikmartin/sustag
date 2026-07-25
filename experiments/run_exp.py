"""Experiment engine: declare experiments as records, run them, log to ONE consolidated log.

An experiment is a QUESTION plus a dict of named recipes to compare against each other on the same
sites and folds. You declare it with @experiment (in exps.py); this module resolves the site pool,
runs the comparison through src.eval.cook, and appends one record per (experiment x task) run to
logs/experiments.jsonl -- the single, git-tracked, append-only history. --summary and the rendered
logs/experiments.md read that log back as Question -> Winner -> Results.

Modes select how many sites (ordered oldest-history-first):
    --test   sites[:2]   (default)     --med   sites[:20]     --full   the full filtered list
    --small  sites[:8]

--extra additionally runs each recipe's slow permutation-importance test (default off).

Usage:
    python run_exp.py                      # all registered experiments, test mode
    python run_exp.py --med --extra
    python run_exp.py --med 17c 18c 19c    # only these, in this order
    python run_exp.py --summary            # print the latest result per experiment
    python run_exp.py --render             # (re)write logs/experiments.md from the log
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

warnings.filterwarnings("ignore")

_HERE = Path(__file__).resolve().parent  # repo/experiments
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))  # so `import exps` works

from src.eval.cook import FAST_XGB, compare_fleet, compare_many
from src.data.access import get_site_ids
from src.features.features import daily_nitrate

_LOG = _ROOT / "logs" / "experiments.jsonl"
_MD = _ROOT / "logs" / "experiments.md"

MODES = {"test": 2, "small": 8, "med": 20, "full": None}
_MIN_OBS = 1500  # a site needs at least this many daily-nitrate observations to enter the pool
_TOP_N = 8  # gain-importance features embedded per recipe in the log

# reg vs clf: (target column, headline metric the winner is chosen by; higher is better)
_TASK = {
    "reg": ("nitrate_con", "lofo_r2"),
    "clf": ("violation", "lofo_auc"),
}

# ── declaration ───────────────────────────────────────────────────────────────

REGISTRY: dict[str, "Experiment"] = {}


@dataclass
class Experiment:
    id: str
    question: str  # REQUIRED: the question this experiment answers (shown first in every summary)
    build: Callable[[str], dict]  # build(task) -> {recipe_name: recipe_fn}; called lazily at run time
    task: str = "reg"  # a run is EITHER 'reg' OR 'clf' -- one task per experiment
    kind: str = "many"  # "many" (cross-site transfer) | "fleet" (per-site)
    sites: list | None = None  # optional fixed site pool (overrides the mode slice)


def experiment(id: str, question: str, task: str = "reg", kind: str = "many", sites=None):
    """Register a single-task experiment. `question` is REQUIRED and heads every summary. `task` is
    'reg' or 'clf' -- one run is either regression or classification, never both. To test the same
    recipe logic under both, register two records (e.g. ids '20' and '20c') sharing one build fn.

    The decorated function takes the task and returns the {name: recipe} dict:  build(task) -> dict.
    Recipes are task-specific (a reg recipe targets nitrate_con; a clf recipe targets violation and
    drops continuous nitrate to avoid leakage), so build receives the experiment's task. It is
    called lazily, so grid factories only run when the experiment does. Returns the fn so one build
    can be registered under several ids."""
    if not question or not question.strip():
        raise ValueError(f"experiment {id!r} must have a non-empty question string.")
    if task not in ("reg", "clf"):
        raise ValueError(f"experiment {id!r}: task must be 'reg' or 'clf', got {task!r}")

    def deco(fn):
        if id in REGISTRY:
            raise ValueError(f"duplicate experiment id {id!r}")
        REGISTRY[id] = Experiment(id=id, question=question.strip(), build=fn, task=task, kind=kind, sites=sites)
        return fn

    return deco


# ── site pool ─────────────────────────────────────────────────────────────────


def build_sites(mode: str) -> list[str]:
    """Sites with >= _MIN_OBS daily-nitrate obs, ordered oldest-history-first, sliced by mode."""
    dated = []
    for s in get_site_ids():
        d = daily_nitrate(s).dropna()
        if len(d) >= _MIN_OBS:
            dated.append((d.index.min(), s))
    dated.sort()  # earliest first, then uid
    ordered = [s for _, s in dated]
    n = MODES[mode]
    return ordered if n is None else ordered[:n]


def _sites_hash(sites) -> str:
    return hashlib.sha1(",".join(sorted(sites)).encode()).hexdigest()[:8]


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


# ── run + log ─────────────────────────────────────────────────────────────────


def _jsonable(v):
    try:
        import numpy as np

        if isinstance(v, np.floating):
            return float(v)
        if isinstance(v, np.integer):
            return int(v)
    except Exception:
        pass
    return v


def _record(exp, task, sites, result, elapsed, extra, mode) -> dict:
    """Build one log record from a compare_* result DataFrame."""
    _, headline = _TASK[task]
    metrics = {r: {k: _jsonable(v) for k, v in row.items()} for r, row in result.to_dict("index").items()}
    recipes = list(metrics)
    scored = [(r, metrics[r].get(headline)) for r in recipes if metrics[r].get(headline) is not None]
    winner = max(scored, key=lambda x: x[1]) if scored else (None, None)
    base = recipes[0]  # first recipe is the baseline; deltas are measured against it (paired)
    delta = {
        r: {headline: round(metrics[r][headline] - metrics[base][headline], 4)}
        for r in recipes
        if metrics[r].get(headline) is not None and metrics[base].get(headline) is not None
    }
    top_gain = {}
    for r, imp in (result.attrs.get("importance") or {}).items():
        try:
            top_gain[r] = list(imp.sort_values(ascending=False).head(_TOP_N).index)
        except Exception:
            pass
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "exp": exp.id,
        "question": exp.question,
        "task": task,
        "kind": exp.kind,
        "mode": mode,
        "n_sites": len(sites),
        "sites_hash": _sites_hash(sites),
        "git_sha": _git_sha(),
        "elapsed_s": round(elapsed, 1),
        "extra": extra,
        "headline_metric": headline,
        "baseline": base,
        "winner": {"recipe": winner[0], "by": headline, "value": _jsonable(winner[1])},
        "delta_vs_base": delta,
        "metrics": metrics,
        "top_gain": top_gain,
    }


def _log(record: dict) -> None:
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def run(exp: Experiment, mode: str = "test", extra: bool = False):
    """Run one experiment (registered OR ad-hoc) and RETURN its metrics DataFrame.

    Resolves the site pool (exp.sites, else the `mode` slice), runs the compare_* driver, prints
    Question -> Winner, appends a record to experiments.jsonl, and refreshes experiments.md. The
    full log record is stashed on result.attrs['record']. Called both by the CLI (via REGISTRY)
    and directly from a notebook (see run_experiment)."""
    sites = exp.sites if exp.sites is not None else build_sites(mode)
    mode_label = mode if exp.sites is None else "custom"  # explicit sites aren't a named mode slice
    driver = compare_fleet if exp.kind == "fleet" else compare_many
    target, _ = _TASK[exp.task]
    recipes = exp.build(exp.task)  # task-specific recipe set (reg vs clf targets differ)
    print(f"\n======= exp {exp.id} [{exp.task}] : {exp.question} =======", flush=True)
    t0 = time.time()
    result = driver(recipes, sites, target_col=target, task=exp.task, extra_importance_test=extra, **FAST_XGB)
    rec = _record(exp, exp.task, sites, result, time.time() - t0, extra, mode_label)
    _log(rec)
    w = rec["winner"]
    print(f"  WINNER: {w['recipe']}  ({w['by']}={w['value']})  ->  logged to {_LOG.name}")
    render_md()
    result.attrs["record"] = rec
    return result


def run_experiment(
    question,
    recipes: "dict | Callable[[str], dict]",
    task: str = "reg",
    *,
    sites=None,
    mode: str = "test",
    kind: str = "many",
    extra: bool = False,
    id: str = "adhoc",
):
    """Run a ONE-OFF experiment directly -- no @experiment, no REGISTRY, no CLI. For a notebook or a
    scratch experiment file: build a {name: recipe} dict, call this, inspect the returned DataFrame.
    Always logs to experiments.jsonl and refreshes experiments.md, exactly like a registered run.

    `recipes` is either a plain {name: recipe_fn} dict (the common one-off case) or a build(task)->dict
    callable (registry style, if you want the recipe set to depend on the task). Each recipe is
    site_uid -> DataFrame INCLUDING the task's target column (nitrate_con for reg, violation for clf) --
    the same contract the registered recipes satisfy. The FIRST recipe is the paired baseline.

    Pass sites=[...] to pin the pool (mode is then ignored, logged as 'custom'); otherwise `mode`
    slices the default oldest-first pool ('test'/'small'/'med'/'full')."""
    if task not in ("reg", "clf"):
        raise ValueError(f"task must be 'reg' or 'clf', got {task!r}")
    if not question or not question.strip():
        raise ValueError("run_experiment needs a non-empty question string.")
    build: "Callable[[str], dict]" = recipes if callable(recipes) else (lambda _task: dict(recipes))
    exp = Experiment(id=id, question=question.strip(), build=build, task=task, kind=kind, sites=sites)
    return run(exp, mode=mode, extra=extra)


# ── read the log back ─────────────────────────────────────────────────────────


def _load_log() -> list[dict]:
    if not _LOG.exists():
        return []
    return [json.loads(line) for line in _LOG.read_text().splitlines() if line.strip()]


def _latest_per_exp(records) -> dict:
    """Last record per (exp, task) -- jsonl append order is chronological."""
    latest = {}
    for r in records:
        latest[f"{r['exp']}[{r['task']}]"] = r
    return latest


def _fmt(v) -> str:
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def _format_run(r: dict, md: bool = False) -> str:
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
        L.append(f"**Winner.** `{w['recipe']}` — {h} = {_fmt(w['value'])}{dtxt}")
        L.append(f"<sub>{r['ts']} · {r['mode']} · {r['n_sites']} sites · git {r.get('git_sha') or '?'}</sub>")
        L.append("")
        L.append("**Results.**")
        L.append("")
        L.append("| recipe | " + " | ".join(cols) + " |")
        L.append("|" + "---|" * (len(cols) + 1))
        for rp in order:
            star = " ⭐" if rp == w["recipe"] else ""
            L.append(f"| {rp}{star} | " + " | ".join(_fmt(metrics[rp].get(c, "")) for c in cols) + " |")
        top = r.get("top_gain", {}).get(w["recipe"])
        if top:
            L.append("")
            L.append(f"_Top features (winner):_ {', '.join(top)}")
    else:
        L.append(f"Question. {r['question']}")
        L.append(f"  Winner.  {w['recipe']} ({h}={_fmt(w['value'])}{dtxt})")
        L.append(f"  Results ({r['mode']}, {r['n_sites']} sites, git {r.get('git_sha') or '?'}):")
        for rp in order:
            star = "*" if rp == w["recipe"] else " "
            L.append(f"   {star} {rp:<24.24s} {h}={_fmt(metrics[rp].get(h))}")
    return "\n".join(L)


def summary() -> None:
    records = _latest_per_exp(_load_log())
    if not records:
        print("No experiment runs logged yet.")
        return
    for key in records:
        print("\n" + _format_run(records[key], md=False))


def render_md() -> None:
    records = _latest_per_exp(_load_log())
    out = ["# Experiment log", "", "_Latest run per experiment. Full append-only history: logs/experiments.jsonl_", ""]
    for key in records:
        out.append(_format_run(records[key], md=True))
        out.append("")
    _MD.parent.mkdir(parents=True, exist_ok=True)
    _MD.write_text("\n".join(out))
    print(f"Wrote {_MD} ({len(records)} experiment-runs)")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    for m in MODES:
        g.add_argument(f"--{m}", dest="mode", action="store_const", const=m)
    ap.add_argument("--extra", action="store_true", help="also run slow permutation importance")
    ap.add_argument("--summary", action="store_true", help="print latest result per experiment and exit")
    ap.add_argument("--render", action="store_true", help="(re)write logs/experiments.md and exit")
    ap.add_argument("ids", nargs="*", help="experiment id(s) to run, in order (REQUIRED; omit to list available ids)")
    args = ap.parse_args()

    import exps  # noqa: F401  -- populates REGISTRY via @experiment

    if args.summary:
        summary()
        return
    if args.render:
        render_md()
        return

    if not args.ids:  # running requires an explicit id -- never run the whole registry implicitly
        print("No experiment id(s) specified. Available experiments:")
        for i, exp in REGISTRY.items():
            print(f"  {i:6s} [{exp.task}]  {exp.question}")
        print("\nSpecify one or more ids to run, e.g.:  python run_exp.py --med 26 26c")
        return

    mode = args.mode or "test"
    ids = args.ids
    unknown = [i for i in ids if i not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown experiment id(s): {unknown}. Registered: {list(REGISTRY)}")
    print(f"Running {len(ids)} experiment(s) in {mode} mode: {ids}")
    for i in ids:
        run(REGISTRY[i], mode, args.extra)  # each run logs + refreshes experiments.md


if __name__ == "__main__":
    # Re-dispatch through the real module name so `import exps` (which does `from run_exp import experiment`) registers into the SAME module object main() reads,
    # not a second copy under __main__.
    import run_exp

    run_exp.main()

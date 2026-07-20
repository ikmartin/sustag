"""Render logs/fulltrain_logs.json -> human-readable markdown.

Run:  python logs/render_logs.py      (paths resolve relative to this file, so any cwd works)

Writes two files next to the JSON log:
  scores.md       -- one headline block per model run: recipe, feature list, score table
  importances.md  -- one block per run: gain + permutation importance table (the heavier data)

The log is a dict keyed by sequential integer (see src/models/train.log_metadata); each entry has
name / recipe / features / target_col / task / xgb / score / importance / importance_perm.
"""

import json
import pandas as pd
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_LOGFILE = _HERE / "fulltrain_logs.json"
_SCORES_MD = _HERE / "scores.md"
_IMPORTANCES_MD = _HERE / "importances.md"


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
    """Render `df` as a markdown table, cells passed through `_fmt`. With `index_label`, the row
    index is emitted as the leading column under that header (the transposed importance table uses
    it for the Gain/Perm row labels)."""
    header = ([index_label] if index_label is not None else []) + [str(c) for c in df.columns]
    lines = [header, ["---"] * len(header)]
    for idx, row in df.iterrows():
        lines.append(([str(idx)] if index_label is not None else []) + [_fmt(v) for v in row])
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)


def _score_table(score):
    # transposed: metric names across the header, a single row of values
    return _make_markdown_table(pd.DataFrame([score]))


def _scores_block(key, entry):
    feats = ", ".join(entry.get("features", [])) or "_(none logged)_"
    return (
        f"# {_model_type(entry)} Model {key}\n\n"
        f"Recipe: {entry.get('recipe', '?')}  \n"  # trailing 2 spaces -> markdown hard line break
        f"Features: {feats}  \n"
        f"Scores:\n\n"
        f"{_score_table(entry.get('score', {}))}\n"
    )


def _importance_table(entry):
    gain = entry.get("importance", {}) or {}
    perm = entry.get("importance_perm", {}) or {}
    if not gain and not perm:
        return "_(no importances logged)_"

    feats = list(gain) + [f for f in perm if f not in gain]  # gain is already gain-ranked
    # transposed: features across the header, a Gain row and a Perm row (missing -> em dash)
    df = pd.DataFrame(
        [
            {f: _fmt(gain[f]) if f in gain else "—" for f in feats},
            {f: _fmt(perm[f]) if f in perm else "—" for f in feats},
        ],
        index=["Gain", "Perm"],
    )
    return _make_markdown_table(df, index_label="Feat")


def _importances_block(key, entry):
    return (
        f"# {_model_type(entry)} Model {key}\n\n"
        f"Recipe: {entry.get('recipe', '?')}\n\n"
        f"Features:  "
        f"{_importance_table(entry)}\n"
    )


def render():
    if not _LOGFILE.exists() or _LOGFILE.stat().st_size == 0:
        raise SystemExit(f"no training log at {_LOGFILE} -- run a training build first")
    with open(_LOGFILE) as f:
        log = json.load(f)
    keys = sorted(log, key=int)  # integer order despite JSON's string keys

    _SCORES_MD.write_text("\n---\n\n".join(_scores_block(k, log[k]) for k in keys) + "\n")
    _IMPORTANCES_MD.write_text("\n---\n\n".join(_importances_block(k, log[k]) for k in keys) + "\n")
    print(f"wrote {_SCORES_MD}  ({len(keys)} models)")
    print(f"wrote {_IMPORTANCES_MD}  ({len(keys)} models)")


if __name__ == "__main__":
    render()

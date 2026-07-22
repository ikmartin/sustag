"""Step 2: the headline contrasts + decision rule.

Two questions:
  RAW      : do connected pairs co-move more than disconnected? (expected yes, but partly seasonality)
  RESIDUAL : does that excess SURVIVE removing the statewide daily mean? (the decisive test -- the
             donor/graph channel only adds value if a neighbour beats `rest_of_state`)
Controls for distance by comparing connected vs disconnected WITHIN matched Euclidean-distance bins.
Writes summary.json.
"""

import json

import numpy as np
import pandas as pd

import common as C

pairs = pd.read_parquet(C.HERE / "pairs.parquet")
conn = pairs[pairs.connected]
disc = pairs[~pairs.connected]


def stat(s):
    return dict(n=int(s.notna().sum()), mean=round(float(s.mean()), 4), median=round(float(s.median()), 4))


summary = {
    "n_sites": int(pd.unique(pairs[["a", "b"]].values.ravel()).size),
    "n_pairs": len(pairs),
    "raw": {"connected": stat(conn.rho_raw), "disconnected": stat(disc.rho_raw)},
    "resid": {"connected": stat(conn.rho_resid), "disconnected": stat(disc.rho_resid)},
}
summary["raw"]["excess_mean"] = round(summary["raw"]["connected"]["mean"] - summary["raw"]["disconnected"]["mean"], 4)
summary["resid"]["excess_mean"] = round(summary["resid"]["connected"]["mean"] - summary["resid"]["disconnected"]["mean"], 4)

# by hop distance (direct=1 vs 2), residual
by_hop = {}
for h in sorted(conn.n_hops.dropna().unique()):
    sub = conn[conn.n_hops == h]
    by_hop[int(h)] = {"raw": stat(sub.rho_raw), "resid": stat(sub.rho_resid)}
summary["by_hop_resid_excess"] = {
    int(h): round(by_hop[h]["resid"]["mean"] - summary["resid"]["disconnected"]["mean"], 4) for h in by_hop
}
summary["by_hop"] = by_hop

# distance-matched: within each Euclidean bin, connected vs disconnected (controls for "closer = more alike")
bins = [0, 25, 50, 100, 150, 250, 1000]
labels = ["0-25", "25-50", "50-100", "100-150", "150-250", "250+"]
pairs = pairs.assign(dbin=pd.cut(pairs.euclid_km, bins=bins, labels=labels))
matched = {}
for lab in labels:
    csub = pairs[(pairs.dbin == lab) & pairs.connected]
    dsub = pairs[(pairs.dbin == lab) & ~pairs.connected]
    matched[lab] = {
        "conn_resid_mean": round(float(csub.rho_resid.mean()), 4) if len(csub) else None,
        "disc_resid_mean": round(float(dsub.rho_resid.mean()), 4) if len(dsub) else None,
        "conn_raw_mean": round(float(csub.rho_raw.mean()), 4) if len(csub) else None,
        "disc_raw_mean": round(float(dsub.rho_raw.mean()), 4) if len(dsub) else None,
        "n_conn": len(csub), "n_disc": len(dsub),
    }
summary["distance_matched"] = matched

(C.HERE / "summary.json").write_text(json.dumps(summary, indent=2))

# ── console readout ─────────────────────────────────────────────────────────────
print("===== HEADLINE =====")
print(f"RAW      Spearman  connected {summary['raw']['connected']['mean']:+.3f}  vs  disconnected "
      f"{summary['raw']['disconnected']['mean']:+.3f}   excess {summary['raw']['excess_mean']:+.3f}")
print(f"RESIDUAL Spearman  connected {summary['resid']['connected']['mean']:+.3f}  vs  disconnected "
      f"{summary['resid']['disconnected']['mean']:+.3f}   excess {summary['resid']['excess_mean']:+.3f}")
print("\nRESIDUAL excess by hop (vs disconnected baseline "
      f"{summary['resid']['disconnected']['mean']:+.3f}):")
for h, ex in summary["by_hop_resid_excess"].items():
    n = by_hop[h]["resid"]["n"]
    print(f"  {h}-hop: connected resid {by_hop[h]['resid']['mean']:+.3f}  (excess {ex:+.3f}, n={n})")
print("\nDistance-matched RESIDUAL means (connected vs disconnected within each km bin):")
print(f"  {'bin':>8} {'conn':>8} {'disc':>8} {'excess':>8}  (n_conn/n_disc)")
for lab in labels:
    m = matched[lab]
    if m["conn_resid_mean"] is None or m["disc_resid_mean"] is None:
        continue
    ex = m["conn_resid_mean"] - m["disc_resid_mean"]
    print(f"  {lab:>8} {m['conn_resid_mean']:+8.3f} {m['disc_resid_mean']:+8.3f} {ex:+8.3f}   ({m['n_conn']}/{m['n_disc']})")
print(f"\nwrote {C.HERE/'summary.json'}")

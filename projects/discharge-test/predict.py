"""Impute daily discharge at nitrate sites that have no gauge, using the model Part 1 validated.

WHY THIS IS THE POINT. 158 of 364 nitrate-bearing records carry no discharge channel, and discharge is the covariate that sets the dilution-versus-flushing regime. NWM exists to fill exactly that gap and, measured, does it worse than this model does (see `discharge_model_report.md`). So the gap is filled here, at no acquisition cost.

WHAT IS DELIBERATELY NOT PREDICTED. The ungauged set includes wells, lakes, estuaries and groundwater stations. A rainfall-runoff response over a contributing basin is the wrong description of every one of them, and the model was neither trained nor validated on anything like them; emitting a number there would be inventing data rather than imputing it. They are recorded as refused, with the reason, rather than silently absent.

THE OUTPUT IS A PROJECT ARTIFACT AND NOT A PUBLISHED CHANNEL. The inventory chapter quarantines NWM as "a simulation wearing the shape of an observation"; a series this project modelled earns the same treatment, so it stays here and any recipe consuming it must carry the distinction.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

import assemble_fast                                            # noqa: E402
import cohort                                                   # noqa: E402
import config                                                   # noqa: E402
import features                                                 # noqa: E402
import run as runner                                            # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[2]))
from data.access import api, config as acfg                     # noqa: E402


def ungauged_nitrate() -> pd.DataFrame:
    """Nitrate-bearing records with no discharge channel, with the reach and area needed to predict."""
    import pyarrow.parquet as pq

    q = api.get_quality()
    nit = sorted(set(q[(q.channel == "nitrate") & (q.verdict == "keep")].code))
    rows = []
    s = (api.get_sensors().dropna(subset=["publishes_under"])
         .drop_duplicates("publishes_under").set_index("publishes_under"))
    vaa = api.get_network("vaa")
    vaa["comid"] = pd.to_numeric(vaa.comid, errors="coerce")
    area = vaa.dropna(subset=["comid"]).set_index(vaa.comid.dropna().astype("int64")).totdasqkm
    for code in nit:
        p = acfg.PUB_WATER / f"{code}.parquet"
        if not p.exists():
            continue
        if any(c.startswith("discharge_") for c in pq.ParquetFile(p).schema_arrow.names):
            continue
        m = s.loc[code] if code in s.index else None
        cid = int(m.comid) if m is not None and pd.notna(m.get("comid")) else None
        rows.append(dict(code=code, comid=cid,
                         site_type=(m.get("site_type") if m is not None else None),
                         totdasqkm=(float(area.get(cid)) if cid in area.index else np.nan)))
    return pd.DataFrame(rows)


def main(assemble: bool = True) -> dict:
    config.ensure_dirs()
    t0 = time.time()
    tgt = ungauged_nitrate()
    ok = tgt[tgt.site_type.isin(config.SITE_TYPES) & tgt.comid.notna() & tgt.totdasqkm.notna()
             & (tgt.totdasqkm > 0)].reset_index(drop=True)
    refused = tgt[~tgt.code.isin(set(ok.code))]
    print(f"ungauged nitrate records: {len(tgt)} | predictable {len(ok)} | refused {len(refused)}", flush=True)
    print(refused.site_type.value_counts().to_string(), flush=True)

    if assemble:
        assemble_fast.run(sites=ok, out_name="weather_ungauged", join_target=False)

    # Train on the whole validated cohort -- the ungauged sites are not in it, so there is nothing to hold out.
    train = features.plausible(cohort.sites())
    pool = features.build_pool(train, stride=config.DAY_STRIDE, quiet=True)
    feat = features.feature_columns(pool)
    m = runner._model()
    m.fit(pool[feat].astype("float32"), pool.y.to_numpy("float64"))
    smear = float(np.mean(np.exp(pool.y.to_numpy("float64") - m.predict(pool[feat].astype("float32")))))
    print(f"trained on {len(pool):,} rows from {pool.site.nunique():,} sites; smear {smear:.3f} "
          f"({(time.time()-t0)/60:.1f} min)", flush=True)

    vaa = cohort.vaa_statics(None).set_index("comid")
    attrs = cohort.attributes(ok.comid.astype("int64").tolist())
    attrs = attrs.set_index("comid") if len(attrs) else None

    out, done = [], 0
    for _, r in ok.iterrows():
        p = config.CACHE / "weather_ungauged" / f"{r.code}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d[config.TARGET] = np.nan                     # features expects the column; it is genuinely absent
        d.to_parquet(p, index=False)
        f = features.site_frame_nodrop(r.code, float(r.totdasqkm), out_name="weather_ungauged")
        if not len(f):
            continue
        f = features.attach_statics(f, int(r.comid), vaa, attrs)
        missing = [c for c in feat if c not in f.columns]
        if missing:
            # LOUD. Silently NaN-filling the model's inputs is exactly how the first prediction pass
            # produced a flat, threefold-low series that raised nothing.
            print(f"    {r.code}: {len(missing)} feature(s) absent, e.g. {missing[:3]}", flush=True)
            for c in missing:
                f[c] = np.nan
        pred_log = m.predict(f[feat].astype("float32"))
        out.append(pd.DataFrame({"code": r.code, "date": f.date.to_numpy(),
                                 "q_mm_day": np.exp(pred_log) * smear,
                                 "q_cms": np.exp(pred_log) * smear * float(r.totdasqkm)
                                          / features.MM_PER_DAY}))
        done += 1
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    res.to_parquet(config.RESULTS / "imputed_discharge.parquet", index=False)
    refused.assign(reason="site type has no rainfall-runoff basin; model neither trained nor validated here"
                   ).to_csv(config.RESULTS / "imputed_discharge_refused.csv", index=False)
    meta = dict(predicted_sites=done, refused_sites=int(len(refused)), rows=int(len(res)),
                smear=smear, trained_rows=int(len(pool)), minutes=round((time.time()-t0)/60, 1))
    (config.RESULTS / "imputed_discharge.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1), flush=True)
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-assemble", action="store_true")
    a = ap.parse_args()
    main(assemble=not a.no_assemble)

# SUPERSEDED — do not run these scripts

**Retired 2026-08-26.** `01_fetch_wqp.py` and `02_audit.py` query the Water Quality Portal's **legacy** endpoint:

```
https://www.waterqualitydata.us/data/Result/search
```

That endpoint **silently drops every USGS result published after 2024-03-11.** It answers HTTP 200 with a valid header row and simply omits the rows — there is no error, no warning, and no way to tell a short response from a complete one. Any conclusion these scripts produced about USGS discrete-sample availability is therefore wrong by an unknown and unbounded margin, and `notes/discrete-sample-report.md` inherits that.

The modern endpoint is `/wqx3/Result/search?dataProfile=narrow`, and it is not a drop-in: results are keyed differently and speciation must be read from `(Result_MeasureUnit, Result_MethodSpeciation)` rather than the unit alone.

**These scripts were not repaired, deliberately.** Their function is subsumed by the WQX source adapter planned at `data/build/acquire/sources/wqx.py` (see `notes/plans/fixes-newdata-audits_Aug26.md`), which brings discrete samples into the pipeline properly — through the registry, with a `nitrate_discrete` channel, snapshotted and verified. A repaired script that nobody runs is a second source of truth about WQP, and the first one is what caused this.

`03_run_exp.py` and `common.py` are left in place: they read the cache rather than the portal, and the unit-conversion table in `common.py` is the reference the adapter should start from.

**The cache in `./cache/` is also suspect** for the same reason. Treat it as pre-2024 only.

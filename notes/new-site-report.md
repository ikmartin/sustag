# New-Site Expansion Report

**Question:** how much can the training set grow beyond the current 23 USGS + ~60 IWQIS sites, and
from where? Compiled 2026-07-22. Live USGS enumeration + codebase usability criteria + a sourced
Minnesota-network assessment. Companion to [future-work-report.md](future-work-report.md) — this is
the "poolable-network inventory" that report's **R8** calls "the first deliverable."

---

## 1. Bottom line

More sites is the right lever: the honest baseline is LOFO over **19 basin families**, and the model
is "a good timer, a bad site-ranker" (`within_r2` 0.42 vs `between_r2` 0.21). New *independent basin
families* attack that gap directly — but only if they clear two bars, and the binding one is not
data availability, it's **covariate coverage**.

- **The 23 current USGS sites are already the near-complete usable set *within the existing grids*.**
  Of 311 national continuous-nitrate sites, only 46 fall in the covariate footprint, 26 clear the
  record-length bar, and 23 also run to the present — i.e. the curation is essentially exhaustive
  in-region, not under-picked.
- **~11 in-footprint USGS sites are unused and worth probing immediately** (2 Illinois at 11 yr, 4
  Missouri, 2 South Dakota, …) — cheapest possible expansion, no new data pipelines.
- **The big pool (~127 usable-record sites) is out-of-footprint** (Corn Belt: IL/IN/WI/KS/MO). Adding
  them means **extending the weather/crops/surplus grids** — crops and surplus are cheap to extend,
  **weather is the real cost** (the grid is built on Iowa IEM cells).
- **Minnesota MPCA network is the best *structural* fit but temporally immature** (2025-new; only 2
  sites have multi-year records; a June-2026 funding cliff). MDA has long records but is mostly
  tile-drain/spring/well sites that **have no surface basin** and break the pipeline.

**Guardrail (from R8):** more sites help `between_r2` only if they add *new families* — nested or
duplicate basins don't. Ship any expansion with an Iowa-only LOFO baseline; pooling has hurt in at
least one published nitrate study (Gorski 2024).

---

## 2. What makes a site "usable" (the bar)

Extracted from `src/build/util/usgs.py`, `filter_sites.py`, and `pipeline_config.toml`:

| # | Criterion | Value / source | Where enforced |
|---|---|---|---|
| 1 | **Continuous nitrate** → a daily series is derivable | pcode `99133` (in-situ), resampled to daily max | `CORE_PCODES`, `daily_nitrate` |
| 2 | **Record length** ≥ 3.92 yr | `lifespan_cutoff = 3.92` | `filter_sites._iwqis_quality` |
| 3 | **Density** ≥ 50 % non-NaN at cadence | `sparsity_cutoff = 0.5` | same (applied to IWQIS; USGS curated by hand) |
| 4 | **Surface basin exists** — not well/spring/karst | `is_groundwater` flag; `siteType=ST` | fetch-skip + `filter_sites` |
| 5 | **Not oversized** | `big_basin` list (e.g. full Missouri/Mississippi) | config `[site_filters]` |
| 6 | **Basin ≥ 75 % covered by covariate grids** | `coverage_threshold = 0.75` | weather build |
| 7 | **Delineable via NLDI** (carries a COMID) | for basin geometry + the new `tot_*` static attrs | `_make_basins` |

Criteria 1–5 are about the *sensor record*; **6–7 are about the covariate footprint**, and #6 is the
one that actually caps expansion.

---

## 3. The covariate footprint is the real constraint

All covariates live on grids clipped to `bbox_wgs84 = [-97.6, 39.8, -89.5, 44.5]` (a box around Iowa
reaching into border states). A site is only usable if its upstream basin is ≥75 % inside that box.

| Covariate | Source | Extent | Cost to extend beyond Iowa |
|---|---|---|---|
| **Weather grid** (`global_node_id`) | IEM 4 km Voronoi + gridMET | **Iowa-bounded** — grid geometry *is* the IEM cell set | **High** — the hard blocker; IEM precip is Iowa-specific, so a national grid means re-basing on CONUS gridMET |
| Crops | USDA CDL / CropScape | CONUS, 2000–2025 | **Low** — clip a wider bbox |
| Surplus | gTREND | CONUS, **2000–2017 only** | **Low–med** — clip wider, but vintage caps at 2017 |

**Implication:** in-footprint sites (incl. southern-MN and border-state basins that fall inside the
box) are cheap to add. Out-of-footprint sites require a weather-grid rebuild — that is the R8
"2–4 weeks," and weather, not nitrate availability, is the gate.

---

## 4. USGS `99133` enumeration — the funnel (live query, 2026-07-22)

Queried `api.waterdata.usgs.gov/ogcapi/v0 time-series-metadata?parameter_code=99133` (no auth needed
for metadata). **All 311 sites report `mg/L as N`** — so there is *no* as-N vs as-NO₃ unit hazard for
USGS (rules out future-work R0(b) for this source).

```
311  national continuous-nitrate (99133) sites
 153  ... with record span >= 3.92 yr           (record-length bar)
  46  ... AND inside the covariate bbox
  26  ... AND span >= 3.92 yr
  23  ... AND still reporting (end >= 2020)   == the current 23 USGS training sites
```

National distribution (top): FL 37, **IL 34, CA 33, IA 27, IN 20**, PA 19, VA 18, MS 12, TX 12,
**WI 11, KS 11, MO 10**. In-footprint + record-length-passing by state: **IA 15, IL 5, MO 4, SD 2.**

### 4a. Immediate candidates — 11 in-footprint sites, unused (Tier 1)

In-bbox, ≥3.92-yr record, **not** in the curated `USGS_SITE_LIST`. Still need the basin-coverage
(#6), not-oversized (#5), and delineation (#7) checks, but no new pipelines:

| site | state | span | ends | note |
|---|---|---|---|---|
| USGS-05446500 | IL | **11.1 yr** | active | longest — high priority |
| USGS-05447500 | IL | **11.1 yr** | active | longest — high priority |
| USGS-401913089534501 | IL | 9.4 yr | active | |
| USGS-05420400 | IL | 4.7 yr | active | Mississippi mainstem near Iowa — check basin size (#5) |
| USGS-05568705 | IL | 4.1 yr | active | |
| USGS-06478513 | SD | 7.2 yr | active | |
| USGS-06481000 | SD | 6.3 yr | 2023 | record ended |
| USGS-06819550 | MO | 4.4 yr | active | |
| USGS-06899900 / 06900050 / 06901500 | MO | 5.9 yr | active | Grand/Thompson R. system — likely one family |

Caveat: several are border-state mainstem sites whose basins may extend **out** of the box and fail
the 75 % coverage test, and the three Missouri sites may collapse to a single basin family (so they
add ~1 family, not 3). Realistic yield: perhaps **4–8 new usable sites, 3–6 new families.**

### 4b. The out-of-footprint pool (~127 sites, Tier 2)

153 record-length-passing sites − 26 in-footprint ≈ **127** usable-record sites outside the box,
concentrated exactly where new families are most valuable (IL, IN, WI, KS, MO Corn Belt). Unlocking
them = the weather-grid rebuild in §3. This is the single largest expansion available and the only
one that meaningfully moves n well past 19 families — but it is a multi-week data-engineering effort.

---

## 5. Other nitrate parameter codes

Confirmed definitions (USGS OGC parameter-codes API) + live national counts:

| pcode | definition | national sites | continuous? | verdict |
|---|---|---|---|---|
| **99133** | Nitrate+nitrite, water, **in situ**, mg/L **as N** | 311 | **Yes** (UV/SUNA/s::can) | **Primary** — keep as-is |
| **99137** | Nitrate, in situ, as N | 7 | Yes | **Add to discovery pcode set** — free +7, nitrate-only, units already as-N |
| **99136** | Nitrate, in situ, **µmol/L** | 1 | Yes | negligible; molar units → ×0.014007 to reach mg/L-as-N if ever used |
| **99124** | Nitrate, **field**, as N | 0 | Yes | defined but **no active sites** — ignore |
| **83554** | Nitrate **load**, **tons/day** | 21 | (flux) | **Skip** — a load, not a concentration; wrong quantity, and load-as-target is ruled out in future-work |
| **00630** | Nitrate+nitrite, unfiltered, as N | 6 (time-series) | **Mixed** | **Query as IV** — some USGS networks store *continuous* nitrate here, not 99133 (see below) |
| **00631** | Nitrate+nitrite, **filtered (lab/grab)**, as N | 72 | **No** (discrete) | **Rate track only** — can't form a daily series; use for the R4 site-level exceedance-*rate* reframing (WQP-style) |
| **00618 / 00620** | Nitrate, lab/grab, as N | 3 / — | No | negligible / discrete only |
| **71850 / 71851** | Nitrate, lab/grab, **as NO₃** | — | No | discrete **and** as-NO₃ (÷4.427 to as-N) — avoid |

**Important pcode caveat — 99133 alone under-counts.** The `991xx` codes are the nominal in-situ
family (of those, only 99133 (311) and 99137 (7) have usable counts; 99136 has 1, 99124 none). **But
some USGS-operated continuous networks store their real-time nitrate under 00630 as an IV series, not
99133.** Verified: **Nebraska's Platte/Elkhorn continuous sensors** (e.g. USGS-06800500 since 2016,
USGS-06805500 since 2012) appear under **00630** with multi-year records, and a Nebraska 99133 query
returns **0 sites**. So the continuous-nitrate enumeration must query **99133 + 99137 + 00630
(hasDataTypeCd=iv)** — a 99133-only pull silently drops the Nebraska network. (Those particular NE
basins are large, western, and will likely fail coverage/oversize, but the code-splitting lesson is
general.) The old WaterQualityWatch nitrate map itself used **pcode 00630** — it was decommissioned
end-2025; discovery is now Water Data for the Nation (the NWIS site/data services remain live).

**Continuous-nitrate scale (active, 2026-07-22):** ~**230–240** active IV nitrate stations nationally
(99133 + the 00630 stragglers), ~**43 % in the Corn Belt / Upper Mississippi–Missouri** (~101 sites;
IL+IA+IN alone ≈ 28 %), with secondary clusters in the Chesapeake (~18 %) and Florida springs
(~13 %). "A couple hundred, Midwest-dominated" — not thousands (the "2,000+ real-time WQ sites"
figure is all parameters, not nitrate).

**Recommendation:** pull `99133` + `99137` for the continuous/daily pipeline (the code already lists
`["99133","99137","83554","00631"]` as `nitrogen_pcodes` in `generate_metadata`, but drop `83554`
(load) and move `00631` to a separate grab-sample track). Reserve `00631`/`00618` + the Water Quality
Portal for the **exceedance-rate** model (future-work R4/R8), which is a different target, not more
rows for the daily model. Unit note: any as-NO₃ code would need ÷4.427 to reach as-N — but 99133 and
the MN sensors are already as-N, so no conversion is needed for the recommended sources.

---

## 6. Minnesota networks

### 6a. MPCA Continuous Nitrate Sensor Network (CNSN) — the natural fit, not yet ripe

- **Structure matches USGS almost exactly:** 37 listed stations (21 installed 2025, scaling to 35),
  all **stream/river sites co-located with DNR/USGS gages** → delineable, gaged upstream basins (CSG
  even publishes drainage area). Sensor is **Hach Nitratax sc** (UV optical), reporting **NO₂+NO₃ as
  N (mg/L)** — *the same quantity as 99133, no unit conversion*. **15-min** continuous. Many
  row-crop catchments (Cedar, Blue Earth, Le Sueur, Cottonwood, Watonwan…).
- **Spring-window target is covered:** sensors deploy at ice-out and run March–Nov, so the April–June
  max-nitrate window is captured; only pre-ice-out/winter is missing, which the target doesn't need.
- **Several southern-MN sites likely sit *inside* the covariate box** (lat ≤ 44.5): e.g. Cedar near
  Austin (~43.7 °N), Zumbro at Kellogg (~44.3 °N), Blue Earth/Le Sueur basins. Their basins drain
  *south* (toward Iowa), so they may pass coverage #6 **without a grid rebuild** — verify grid
  population at the northern bbox edge and the surplus-2017 vintage.
- **Blockers:** (1) **record length** — 2025-new; only **Cedar** and **Zumbro** (~10 yr legacy
  sensors) meet the 3.92-yr bar today. (2) **Funding cliff** — network funding expires **June 30
  2026**; 2026-season continuity is at risk. (3) **Access** — interactive DNR CSG portal
  (`dnr.state.mn.us/waters/csg`), no confirmed public API (an undocumented `csg.cgi?mode=get_data`
  endpoint may allow scripting — probe it); real-time is provisional with a ~2-yr lag to finalized.

**Verdict:** the *cleanest* external match structurally, but **not actionable at scale until ~2027+**
(record maturity) and contingent on 2026 funding. Add **Cedar + Zumbro now** (long records, likely
in-footprint); revisit the rest in ~2 years.

### 6b. MDA sensors (Discovery Farms / Root River F2S / DWSMAs) — long records, wrong geometry

- **Records are strong** (Root River F2S since 2009/2010; year-round 15-min; Nitrate-N as N via
  OneRain), **strongly agricultural** — attractive on paper.
- **Fatal friction: site type.** Most of the ~29 sensors are **tile-drain, spring, well, cave-drip,
  or pond** — **no delineable surface drainage basin**, so they break the "delineate upstream
  catchment per site" assumption (criterion #4/#7). Only the handful of **true in-stream subwatershed
  outlets** (RRFSP + 3 MDA stream sites: Bridge Creek, Crystal Creek, S. Branch Root R.) are usable,
  and those basins are **very small** (tens–few-thousand acres) vs. typical USGS basins.
- **Access:** OneRain interactive map/dashboard (`mda.onerain.com`); public scriptable export
  unconfirmed; no published coordinate table.

**Verdict:** harvest only the **~3–6 true in-stream sites**; skip the tile/spring/well majority. Not
a bulk source. (The tile-drain sites are, however, interesting for the *loading-source* modeling in
future-work, not as target-bearing basins.)

---

## 7. Recommendations (sequenced)

1. **Tier 1 — in-footprint USGS (days).** Run the §4a 11 candidates through basin delineation +
   coverage/oversize/groundwater checks; add those that pass to `USGS_SITE_LIST`. Add `99137` to the
   discovery pcode set. Expected: **4–8 sites, 3–6 new families, zero new pipelines.** Prioritize the
   two 11-yr Illinois sites.
2. **Tier 1.5 — southern-MN legacy (days).** Verify grid coverage at the north bbox edge, then add
   **Cedar (Austin)** and **Zumbro (Kellogg)** if in-footprint — the only MN sites with usable
   records today. Requires a small CSG-portal scraper.
3. **Tier 2 — grid extension (2–4 weeks).** The high-value move: rebuild the weather grid on CONUS
   gridMET (crops/surplus extend cheaply) to unlock ~127 out-of-footprint USGS sites across the Corn
   Belt — the real n≫19 injection. Gate the decision on whether Tier-1 families already lift
   `between_r2`.
4. **Tier 3 — MN CNSN at maturity (~2027).** Revisit the full 35-site MPCA network once records pass
   3.92 yr *and* 2026 funding is confirmed. Structurally it is the best non-USGS match.
5. **Separate track — exceedance-rate model (future-work R4/R8).** Use WQP + `00631`/`00618` grab
   stations across the Mississippi basin for the *site-level rate* target — attacks n=19 without the
   continuous-data or full-grid requirements. Different modeling objective; scope independently.

**Every tier ships with an Iowa-only LOFO baseline** so a pooling regression can be detected.

---

## Appendix — reproducing the enumeration

```python
from dataretrieval import waterdata          # metadata needs no API key
df,_ = waterdata.get_time_series_metadata(parameter_code="99133", skip_geometry=False)
# per-site: min(begin_utc), max(end_utc) -> span_yr; geometry -> lon/lat; unit_of_measure
# usable = span_yr>=3.92 & in bbox([-97.6,39.8,-89.5,44.5]) & siteType stream & not oversized
```

For a sharper continuous-vs-grab and stream-vs-well split, the legacy site service adds
`data_type_cd` (iv/dv vs qw), `count_nu`, and `siteType=ST`:
`waterservices.usgs.gov/nwis/site/?parameterCd=99133,99137,00630&siteType=ST&hasDataTypeCd=iv&seriesCatalogOutput=true&outputDataTypeCd=iv,dv&format=rdb`
(loop `stateCd` for national coverage). **Query 99133 + 99137 + 00630-as-IV**, not 99133 alone, or you
drop USGS continuous networks (Nebraska) that store under 00630. Data pulls (not metadata) require
`api-keys.toml → usgs` PAT.

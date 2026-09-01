"""Render the audit to `report.md`. Always generated, never hand-edited.

The prose that INTERPRETS this -- what the themes mean, the do-not-double-count list, what to use when -- is hand-written in `notes/book/data_inventory_chapter.md` and cites this file. The division is deliberate: numbers that go stale the moment a pipeline runs belong somewhere regenerable, and judgements that statistics cannot make belong somewhere a person signs.
"""

from __future__ import annotations

import datetime as dt
import tomllib

import pandas as pd

from . import agreement, config, exposure, geometry, images, sources


def _md_table(d: pd.DataFrame, cols=None) -> str:
    d = d[cols] if cols else d
    if not len(d):
        return "_(nothing to report)_\n"
    head = "| " + " | ".join(str(c) for c in d.columns) + " |"
    rule = "|" + "|".join("---" for _ in d.columns) + "|"
    rows = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
            for r in d.itertuples(index=False)]
    return "\n".join([head, rule, *rows]) + "\n"


def themes() -> dict:
    return tomllib.load(open(config.THEMES, "rb")) if config.THEMES.exists() else {}


def _theme_section() -> str:
    t = themes()
    if not t:
        return "_(no theme registry)_\n"
    rows = []
    for name, spec in t.items():
        src = spec.get("sources", [])
        cand = spec.get("candidates", [])
        verdict = ("absent" if not src else "thin" if len(src) <= 1 else "covered")
        rows.append(dict(theme=name, sources=len(src), verdict=verdict,
                         members=", ".join(src) or "—",
                         candidates=", ".join(cand) or "—"))
    d = pd.DataFrame(rows).sort_values(["verdict", "theme"])
    out = [_md_table(d, ["theme", "verdict", "sources", "members", "candidates"])]
    thin = d[d.verdict != "covered"]
    if len(thin):
        out.append("\n**Thin or absent themes are the finding.** These are enumerated from the domain "
                   "rather than from our holdings, so a short list here means a real gap in what the "
                   "project can see, not a gap in the registry:\n")
        for r in thin.itertuples():
            out.append(f"- **{r.theme}** ({r.verdict}) — {t[r.theme].get('description','')}\n")
    return "".join(out)


def render() -> str:
    """The whole report as markdown."""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    g = exposure.substores()
    surface = exposure.access_surface()
    find = exposure.findings()

    parts = [
        "# Data insights\n\n",
        f"_Generated {now} by `python -m data.insights`. **Never hand-edit this file** — it is "
        f"regenerated after every pipeline run. Interpretation lives in the inventory chapter's "
        f"`## Data Themes` section, which cites this._\n\n",
        "---\n\n## Tier 0 — what is on disk against what the access layer exposes\n\n",
        f"**{g.bytes.sum() / 1e9:.1f} GB across {int(g.n_files.sum()):,} files** in "
        f"{g.layer.nunique()} store layers.\n\n",
    ]

    show = g.assign(GB=(g.bytes / 1e9).round(2)).drop(columns=["bytes"])
    parts.append(_md_table(show, ["layer", "substore", "n_files", "GB", "status", "readers"]))

    parts.append("\n### Reachable by nobody\n\n")
    parts.append("**Three states, not two.** A substore with no access reader is not a finding on its own: "
                 "`acquired/facilities` has none and is read by `derive/structures`, whose product is "
                 "published — flagging it would send a reader to fix something that works, and one false "
                 "row makes the true ones easy to dismiss. The finding is data reachable by **nobody**: no "
                 "access function, and no mention anywhere in the live `data/` source.\n\n")
    orph = g[g.status == "ORPHANED"]
    if len(orph):
        parts.append(_md_table(orph.assign(GB=(orph.bytes / 1e9).round(3)),
                               ["layer", "substore", "n_files", "GB"]))
    else:
        parts.append("_(nothing is orphaned)_\n")
    parts.append("\n`derived/` is intermediate by design — publication projects it into `published/` — so "
                 "a derived substore without a reader is the architecture working. `src/` is excluded from "
                 "the consumption scan on purpose: it is the retired predecessor, and data referenced only "
                 "there is precisely what this is built to surface.\n")

    parts.append("\n### The access surface\n\n")
    parts.append(_md_table(surface, ["function", "signature", "declared_paths"]))

    parts.append("\n---\n\n## Themes\n\n")
    parts.append(_theme_section())

    parts.append("\n---\n\n## Tier 1 — what each source is\n\n")
    parts.append(_md_table(sources.table(),
                           ["source", "theme", "native_grain", "cadence", "span", "lineage", "store"]))

    parts.append("\n### Do not double-count\n\n")
    parts.append("Pairs that are **not independent**, established by reading lineage rather than by "
                 "correlating values. No statistic can distinguish *agrees because both are right* from "
                 "*agrees because both are the same data*, so this list is traced by hand and is the "
                 "reason `lineage` is a column above.\n\n")
    parts.append(_md_table(sources.double_count_table(), ["a", "b", "why"]))

    parts.append("\n---\n\n## Tier 2 — comparison within a theme\n\n")
    parts.append("### Does the source even reach the cohort?\n\n")
    parts.append("Asked first, because a redundancy or agreement statistic computed on the rows a source "
                 "happens to cover says nothing about the rows it does not. Span is reported against the "
                 "water record's 2008–2026 window, so a source ending in 2017 reads as the slowly-varying "
                 "descriptor it is rather than as a contemporaneous covariate.\n\n")
    try:
        parts.append(_md_table(sources.coverage_table(),
                               ["source", "reached", "of", "frac", "span", "verdict"]))
    except Exception as e:                                # noqa: BLE001
        parts.append(f"_(coverage unavailable: {type(e).__name__}: {e})_\n")

    parts.append("\n### Does a source vary *within* a catchment?\n\n")
    parts.append("The decisive cheap test, and the one that generalises: a source whose native cell is "
                 "larger than the catchment it is reduced to delivers a **basin-level constant**, "
                 "whatever resolution it advertises. Answerable from geometry alone — no data read.\n\n")
    try:
        parts.append(_md_table(geometry.resolution_table(),
                               ["product", "cell_km2", "frac_smaller_than_cell", "verdict"]))
        parts.append("\n**Do not pay for spatial resolution in a gridded covariate at this grain.** "
                     "It is why OpenET's 30 m advantage over TerraClimate's 4 km is worth nothing here, "
                     "and why comparing those two on resolution compares a quantity neither delivers.\n")
    except Exception as e:                                # noqa: BLE001 -- the network may not be on disk
        parts.append(f"_(resolution table unavailable: {type(e).__name__})_\n")

    parts.append("\n#### Measured, not inferred from cell size\n\n")
    parts.append("The table above answers the question from geometry and never reads a value, so it cannot "
                 "see a source whose values are smooth for reasons other than cell size. This one decomposes "
                 "the actual catchment-grain variance into between-basin and within-basin parts over whole "
                 "sampled HUC8s — whole basins, because a scattered catchment sample would have almost no two "
                 "catchments sharing a basin and the within-basin term would be undefined. **`within` is the "
                 "share a basin-level aggregate destroys**, so a source near zero is already a basin constant "
                 "and its native resolution buys nothing after aggregation.\n\n")
    try:
        vt = geometry.variance_table()
        parts.append(_md_table(vt, ["source", "n", "basins", "within", "between", "verdict"]))
        fig = images.within_basin_variance(vt)
        if fig:
            parts.append(f"\n![within-basin variance](images/{fig})\n")
    except Exception as e:                                # noqa: BLE001
        parts.append(f"_(variance table unavailable: {type(e).__name__}: {e})_\n")

    parts.append(_tier2_battery())
    parts.append("\n### Not measured, deliberately\n\n")
    parts.append("**There is no tier measuring predictive power.** That is a property of a task, not of "
                 "the data, and recording it here would turn an inventory into a record of one model's "
                 "preferences on one cohort at one date.\n")
    return "".join(parts)


def _theme_matrices(comids) -> str:
    """A redundancy matrix per theme, for the themes with a probe registry — and a named reason for the rest."""
    parts = ["\n### Redundancy within a theme\n\n",
             "One representative column per source per theme, rank-correlated. **The block structure is the "
             "message, not any single coefficient.** A theme names sources rather than columns, so the "
             "correspondence is a hand-written judgement in `sources.THEME_PROBES` — picking one column over "
             "another is a claim about what the theme means, which is not derivable.\n\n"]
    done = []
    for theme in sources.THEME_PROBES:
        try:
            m = sources.theme_matrix(theme, comids)
        except Exception as e:                            # noqa: BLE001
            parts.append(f"_({theme}: unavailable — {type(e).__name__})_\n\n")
            continue
        if m.empty:
            parts.append(f"_({theme}: fewer than two probes resolved against the stores)_\n\n")
            continue
        fig = images.redundancy_matrix(m, theme)
        parts.append(f"**{theme}** — {len(m)} probes\n\n")
        if fig:
            parts.append(f"![{theme}](images/{fig})\n\n")
        done.append(theme)
    # WHY A THEME HAS NO MATRIX DIFFERS BY THEME, so the reason is per-theme rather than one blanket
    # sentence. An earlier version claimed all of them carried "fewer than two comparable sources", which
    # was simply false for soils and physiography -- both had the sources and merely lacked a registry.
    why = {
        "meteorological_forcing": "one source (gridMET); a covariate cannot be redundant with itself",
        "antecedent_moisture_and_snow": "one source (gridMET pentad drought, 25 aggregates); SNODAS would be the second",
        "legacy_nitrogen": "one source (StreamCat `n_leg`)",
        "groundwater_and_residence_time": "one source (Wieczorek Hydrologic)",
        "conservation_practices": "one source, and the theme is a known observational gap",
        "instream_processing": "its members are SPARSE point layers (dams, waterbodies) where absent is "
                               "not zero, so a correlation over catchments would be measuring where "
                               "structures exist rather than whether the sources agree",
        "point_sources_and_structures": "same reason — sparse point layers, where a shared zero is an "
                                        "absence of structures rather than an agreement",
        "surface_hydrology_and_flow": "its second member is the discharge CHANNEL, a time series rather "
                                      "than a catchment-keyed column, so the two are not commensurable here",
        "water_quality_observations": "its members are the observations themselves, not covariates; "
                                      "agreement between them is the identity layer's question",
    }
    missing = [t for t in report_themes() if t not in sources.THEME_PROBES]
    if missing:
        parts.append("\n**Themes with no matrix, and why** — the reason differs, so it is given per theme "
                     "rather than as one sentence.\n\n")
        parts.append(_md_table(pd.DataFrame(
            [dict(theme=t, reason=why.get(t, "no probe registry entry yet")) for t in missing])))
    return "".join(parts)


def report_themes() -> list:
    return list(themes().keys())


def _tier2_battery() -> str:
    """Incremental information, temporal decomposition, and missingness, over a seeded catchment sample."""
    parts = []
    try:
        comids = agreement.sample_comids()
        wide = agreement.nutrients_wide(comids)
    except Exception as e:                                # noqa: BLE001 -- the product may not be published
        return f"\n### Agreement, time and missingness\n\n_(unavailable: {type(e).__name__}: {e})_\n"

    n_cat = wide.index.get_level_values("comid").nunique()
    parts.append(f"\n### Does a source add anything the others do not?\n\n")
    parts.append(f"Measured on the nutrient budget over a seeded random sample of **{n_cat:,} catchments** "
                 f"({len(wide):,} catchment-years). Pairwise correlation cannot answer this — two products "
                 "can each correlate weakly with a third and still be jointly redundant with it — so the "
                 "measure is each product's **R² regressed on all the others**. High means the product is "
                 "reconstructible from what we already hold.\n\n")
    inc = agreement.incremental_information(wide, exclude=("fixation_ag", "uptake_ag"))
    parts.append(_md_table(inc, ["product", "r2_given_others", "verdict"]))
    parts.append("\nThe two gTREND closure totals are excluded: they are their own parts summed, so "
                 "including them would report a perfect fit and say nothing. Rank correlations for the "
                 "most-agreeing pairs, reported alongside and never instead of the above:\n\n")
    parts.append(_md_table(agreement.top_pairs(wide, 6), ["a", "b", "spearman"]))

    parts.append("\n### Is an annual product actually a time series?\n\n")
    parts.append("Variance on the (catchment × year) panel splits into a between-catchment part (a static "
                 "map), a between-year part (a national trend applied everywhere), and the interaction — "
                 "and **only the interaction is spatiotemporal information**, the part a model cannot get "
                 "from a static covariate plus a year dummy.\n\n")
    vd = agreement.variance_decomposition(wide)
    parts.append(_md_table(vd, ["product", "space", "time", "interaction", "verdict"]))
    fig = images.temporal_decomposition(vd)
    if fig:
        parts.append(f"\n![variance decomposition](images/{fig})\n")
    parts.append(_theme_matrices(comids))

    parts.append("\n### Is absence random?\n\n")
    parts.append("`absent is not zero` protects the join; it does not tell a model that a gap is "
                 "informative. Comparing the catchment areas of covered against uncovered reaches says "
                 "whether the gap is structured.\n\n")
    try:
        parts.append(_md_table(agreement.missingness(comids),
                               ["store", "n", "missing", "frac_missing",
                                "median_area_present", "median_area_missing"]))
    except Exception as e:                                # noqa: BLE001
        parts.append(f"_(missingness unavailable: {type(e).__name__})_\n")

    parts.append("\n#### Is nullity predictable from *position*?\n\n")
    parts.append("The stronger form of the question, and the one that decides whether imputing a gap "
                 "smuggles geography into the feature. `share_in_worst_decile` is the fraction of nulls "
                 "falling in the tenth of basins with the highest null rates — **0.10 is what a random "
                 "scatter gives**. The decile is chosen after seeing the data, so the statistic is biased "
                 "upward when nulls are few; rows with fewer nulls than three per basin are reported "
                 "inconclusive rather than structured.\n\n")
    try:
        parts.append(_md_table(agreement.missingness_by_position(),
                               ["family", "column", "null_rate", "n_null",
                                "share_in_worst_decile", "verdict"]))
    except Exception as e:                                # noqa: BLE001
        parts.append(f"_(position probe unavailable: {type(e).__name__}: {e})_\n")
    return "".join(parts)


def write() -> str:
    config.IMAGES.mkdir(parents=True, exist_ok=True)
    text = render()
    config.REPORT.write_text(text)
    return text

"""Curated USGS parameter-code registry for candidate nitrate covariates.

Why this file exists: `src/build/util/usgs.py:CORE_PCODES` pulls 8 pcodes. That set silently misses data for two reasons. (1) **One quantity, many pcodes** -- turbidity alone is spread over 5 pcodes that are actually populated in our region (63680 FNU, 63675 NTU, 72213 FBRU, 63682 FBU, 00070 JTU), because USGS codes the *optical geometry of the instrument*, not the quantity. A query for 63680 alone finds 159 regional sites; the union finds more. The same failure mode is already documented for nitrate itself in notes/new-site-report.md (99133 vs 00630 -- a 99133-only query drops the entire Nebraska continuous network). (2) **Lab/discrete twins** -- pH, specific conductance and most nutrients have a separate discrete-sample pcode (00403, 90095, 00631 ...) served by a different API (Samples, not time-series).

FAMILIES maps a physical quantity -> every pcode that carries it, tagged by how it is measured:
    "insitu"   continuous sensor time series      -> waterdata.get_continuous / get_time_series_metadata
    "lab"      discrete sample, laboratory        -> waterdata.get_samples
    "field"    discrete sample, field measurement -> waterdata.get_samples
    "flux"     a LOAD (mass/time), not a concentration -- carries discharge inside it

`flux` is called out because loads are ruled out as a target and are dangerous as features:
notes/future-work-report.md flags L-Q correlation as spurious (discharge is a component of load), so a load feature smuggles discharge in multiplicatively. Kept in the registry for completeness, excluded from the recommended pull sets.

RATIONALE gives the mechanistic reason each family is a candidate, with the citation carried in REPORT.md. Nothing here is pin-legal on its own -- see REPORT.md section 5.
"""

# family -> {pcode: (short_name, kind)}
FAMILIES: dict[str, dict[str, tuple[str, str]]] = {
    # ── already in CORE_PCODES ────────────────────────────────────────────────
    "nitrate": {
        "99133": ("nitrate_con", "insitu"),        # NO3+NO2 as N, in situ  (the target)
        "99137": ("nitrate_only", "insitu"),       # nitrate (no nitrite), in situ
        "00630": ("nitrate_con_iv", "insitu"),     # some networks store continuous nitrate here
        "00631": ("nitrate_wf_lab", "lab"),        # filtered, lab/grab
        "00618": ("nitrate_wf_lab2", "lab"),
    },
    "discharge": {"00060": ("discharge", "insitu")},
    "stage": {
        "00065": ("stage", "insitu"),
        "63160": ("stream_level_navd88", "insitu"),
        "63158": ("stream_level_ngvd29", "insitu"),
    },
    "water_temp": {
        "00010": ("temp_water", "insitu"),
        "00011": ("temp_water_f", "insitu"),       # deg F twin
    },
    "spec_cond": {
        "00095": ("spec_cond", "insitu"),
        "90095": ("spec_cond_lab", "lab"),
    },
    "diss_oxy": {
        "00300": ("diss_oxy_con", "insitu"),
        "00301": ("diss_oxy_sat", "insitu"),
        "62971": ("diss_oxy_lab", "lab"),
    },
    "ph": {
        "00400": ("ph", "insitu"),
        "00403": ("ph_lab", "lab"),
        "00408": ("ph_wf_lab", "lab"),
        "72429": ("ph_wf_field", "field"),
    },
    # ── NOT in CORE_PCODES: the additions this report is about ───────────────
    "turbidity": {
        "63680": ("turbidity_fnu", "insitu"),      # monochrome near-IR, 90deg  (modern standard)
        "63675": ("turbidity_ntu", "insitu"),      # broadband white light, 90deg
        "72213": ("turbidity_fbru", "insitu"),     # backscatter + 90deg ratio
        "63682": ("turbidity_fbu", "insitu"),      # formazin backscatter
        "00070": ("turbidity_jtu", "insitu"),      # legacy Jackson turbidity units
        "61028": ("turbidity_field", "field"),
        "82079": ("turbidity_lab", "lab"),
    },
    "fdom": {
        "32295": ("fdom_qse", "insitu"),           # ug/L quinine sulfate equivalent
        "32322": ("fdom_rfu", "insitu"),           # relative fluorescence units
        "32330": ("fdom_mgl", "insitu"),
    },
    "chlorophyll": {
        "32316": ("fchl_ugl", "insitu"),
        "32315": ("fchl_rfu", "insitu"),
        "32318": ("chlorophylls_insitu", "insitu"),
        "62361": ("chlorophyll_insitu_dep", "insitu"),   # DEPRECATED by USGS
        "32209": ("chl_a_fm", "lab"),
        "70953": ("chl_a_phyto", "lab"),
    },
    "phycocyanin": {
        "32319": ("fpc_ugl", "insitu"),
        "32321": ("fpc_rfu", "insitu"),
    },
    "sediment": {
        "80154": ("ssc_mgl", "lab"),               # suspended-sediment concentration
        "99409": ("ssc_est_mgl", "insitu"),        # surrogate-estimated SSC (regression output)
        "00530": ("susp_solids_mgl", "lab"),
        "80155": ("ssc_load", "flux"),
        "91055": ("susp_solids_load", "flux"),
    },
    "phosphorus": {
        "00665": ("tp_mgl", "lab"),                # total P, unfiltered
        "00666": ("tp_diss_mgl", "lab"),
        "00671": ("orthop_wf_mgl", "lab"),
        "51289": ("orthop_insitu", "insitu"),
        "99416": ("tp_est_mgl", "insitu"),
        "91050": ("tp_load", "flux"),
        "81206": ("tp_diss_load", "flux"),
    },
    "reduced_n": {
        "00625": ("tkn_mgl", "lab"),               # ammonia + organic N (unfiltered)
        "00608": ("nh3_wf_mgl", "lab"),            # ammonia, filtered
        "91057": ("tkn_load", "flux"),
        "62961": ("nh3_load", "flux"),
    },
    "chloride": {
        "00940": ("chloride_mgl", "lab"),
        "70290": ("chloride_load", "flux"),
    },
    "velocity": {
        "72255": ("mean_velocity", "insitu"),
        "72254": ("sensor_velocity", "insitu"),
    },
    # ── land-surface / subsurface state (co-located USGS instruments) ────────
    "soil_moisture": {
        "74207": ("soil_moisture_pct", "insitu"),
        "72181": ("soil_moisture_frac", "insitu"),
    },
    "soil_temp": {
        "72253": ("soil_temp_c", "insitu"),
        "62846": ("soil_temp_f", "insitu"),
    },
    "groundwater": {
        "72019": ("gw_depth_lsd", "insitu"),       # depth to water below land surface
        "62611": ("gw_elev_navd88", "insitu"),
        "62610": ("gw_elev_ngvd29", "insitu"),
    },
    "precip_site": {
        "00045": ("precip_in", "insitu"),
        "99772": ("precip_mm", "insitu"),
    },
}

# Mechanistic rationale -- the "why" behind each candidate. Citations in REPORT.md section 2.
RATIONALE: dict[str, str] = {
    "turbidity": "Particulate-N mobilization and storm-flushing onset; the workhorse surrogate in USGS real-time regression models. Distinguishes surface-wash events from subsurface tile delivery.",
    "fdom": "Proxy for dissolved organic carbon, the electron donor required for heterotrophic denitrification; fDOM and nitrate commonly move in opposite directions during events.",
    "spec_cond": "Integrates total dissolved ions; an effective nitrate surrogate in baseflow-dominated agricultural systems, where groundwater/tile water carries both salts and nitrate.",
    "diss_oxy": "Denitrification is anoxic/suboxic; DO > ~2 mg/L inhibits it, so DO gates whether nitrate is conservatively transported or removed in-stream.",
    "water_temp": "Controls microbial kinetics of nitrification/denitrification and DO solubility; the dominant seasonal driver of in-stream N processing rates.",
    "ph": "Sets NH3/NH4+ equilibrium and microbial enzymatic efficiency across the N cycle.",
    "chlorophyll": "Algal biomass proxy; quantifies autotrophic assimilation drawdown of dissolved inorganic N from the water column.",
    "phycocyanin": "Cyanobacteria-specific pigment; a second biological-uptake proxy where cyanobacteria dominate assimilation.",
    "sediment": "Particulate-N and erosion signal; co-varies with turbidity but is a mass concentration rather than an optical proxy.",
    "phosphorus": "Co-nutrient with a contrasting transport pathway (P is particulate/surface-bound, N is dissolved/subsurface), so the N:P contrast discriminates delivery route.",
    "reduced_n": "The reduced N pool (ammonium + organic N) that nitrifies into the nitrate being predicted; a source term rather than a correlate.",
    "chloride": "Conservative anion, effectively inert in-stream; separates the conservative-transport component of specific conductance from the reactive nitrate component and flags wastewater influence.",
    "discharge": "Primary mass-transport driver; Iowa nitrate is mobilizing (C-Q exponent b = +0.61 on this project's own gauged sites), so concentration rises with flow.",
    "velocity": "Sets in-stream residence/contact time, which governs how much denitrification can occur along a reach.",
    "soil_moisture": "Antecedent moisture is a threshold control on tile-drain activation; tile runoff only initiates above a near-saturation soil-water content.",
    "groundwater": "Water-table depth primes tile activation and sets the baseflow pathway that carries the majority of Iowa's annual nitrate export.",
    "precip_site": "Gauge-co-located precipitation; largely redundant with the existing gridded IEM/gridMET weather layer.",
    "stage": "Water-surface elevation; a discharge proxy where no rating exists.",
    "nitrate": "The target itself (and, at a neighbouring site, the donor signal for network features).",
}

CORE_PCODES = {"00010", "99133", "00300", "00301", "00400", "00095", "00060", "00065"}


def all_pcodes(kinds=("insitu",), families=None) -> dict[str, str]:
    """{pcode: short_name} across FAMILIES, filtered by measurement kind."""
    out = {}
    for fam, d in FAMILIES.items():
        if families and fam not in families:
            continue
        for pc, (name, kind) in d.items():
            if kind in kinds:
                out[pc] = name
    return out


def pcode_family() -> dict[str, str]:
    """{pcode: family}."""
    return {pc: fam for fam, d in FAMILIES.items() for pc in d}


def pcode_kind() -> dict[str, str]:
    return {pc: k for d in FAMILIES.values() for pc, (_, k) in d.items()}


def pcode_name() -> dict[str, str]:
    return {pc: n for d in FAMILIES.values() for pc, (n, _) in d.items()}


if __name__ == "__main__":
    fams = sorted(FAMILIES)
    print(f"{len(fams)} families, {len(pcode_family())} pcodes")
    for f in fams:
        ins = [p for p, (_, k) in FAMILIES[f].items() if k == "insitu"]
        lab = [p for p, (_, k) in FAMILIES[f].items() if k in ("lab", "field")]
        flux = [p for p, (_, k) in FAMILIES[f].items() if k == "flux"]
        new = [p for p in FAMILIES[f] if p not in CORE_PCODES]
        print(f"  {f:15s} insitu={len(ins):2d} lab={len(lab):2d} flux={len(flux):2d}  not-in-CORE={len(new):2d}")

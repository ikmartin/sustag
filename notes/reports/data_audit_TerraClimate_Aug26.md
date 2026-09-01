# Data audit — TerraClimate, and what it says about the soil-moisture family

**Verdict: do not acquire TerraClimate.** Not now, and not as the gate it was proposed to be. The audit was meant to be cheap and it was — but it answers a different question than the one that was asked, and the reason is worth more than the verdict.

Audited 2026-08-26. **Nothing was downloaded.** The decisive measurement is a property of our own catchment layer.

## What the source is

`/thredds/catalog/TERRACLIMATE_ALL/data/` on the same THREDDS server we already call for gridMET. **3,192 files, 1950–2025, monthly, ~4 km global**, one file per (variable, year), anonymous, OPeNDAP alongside. Fourteen observed variables, of which four motivated the interest: **`soil`** (soil moisture), **`q`** (runoff), **`swe`** (snow water equivalent), **`def`** (climatic water deficit).

## Why it was gated

Not on its own merits. It was proposed as **the cheap instrument for an expensive question**: NLDAS-2 (Earthdata login, ~163k authenticated requests) and OpenET (API key, quotas) are both gated behind "does soil-moisture-family information earn anything here", and TerraClimate appeared to answer that for free.

## What the audit did

The plan's decisive test was the **within-basin variance ratio** — does a source vary *within* a catchment, or is it delivering a basin-level constant wearing a covariate's name? For a gridded product that question is answerable from geometry alone, with no data at all.

Measured over all **1,478,688 catchments** in the network (median area **1.38 km²**, mean 2.62):

| product | cell area | catchments smaller than one cell |
|---|---|---|
| AORC | ~1 km² | **40.7%** |
| **TerraClimate** | **~16 km²** | **98.4%** |
| gridMET | ~19 km² | 98.8% |
| NLDAS-2 | ~146 km² | **100.0%** |

## What the audit determines

**TerraClimate's spatial resolution is immaterial at our grain, and so is NLDAS-2's.** 98.4% of our catchments are smaller than a single TerraClimate cell — such a catchment straddles at most two to four cells and usually sits inside one, so within-catchment spatial variance is essentially zero by construction. Any comparison of these products on *resolution* is a comparison of a quantity neither of them delivers here.

**That is not a defect for soil moisture.** A catchment-level soil-moisture value is exactly the quantity of interest; nobody needs sub-catchment structure in it. The finding is narrower and more useful: **do not pay anything for resolution in this family.** It also disposes of OpenET's headline advantage — 30 m against 4 km — which vanishes entirely under catchment aggregation.

**TerraClimate cannot serve as the gate.** The question NLDAS-2 was gated behind is an *antecedent* one — how wet was the profile in the **days** before a storm. TerraClimate is **monthly**. A monthly soil-moisture value cannot distinguish the week before a storm from the week after, so a null result from it would not be evidence against hourly soil moisture, and a positive result would not be evidence for it. It answers a different question.

**What TerraClimate would be good for**, if wanted on its own terms: cheap seasonal context — how wet this season was relative to normal — at zero authentication cost, back to 1950. That is a real but different proposition from the one it was gated to test.

## What the audit does NOT determine

- **Whether soil moisture helps at all.** No model was run; that is a modelling question and deliberately out of scope.
- **Whether NLDAS-2's hourly cadence earns its acquisition cost.** The intended cheap proxy does not exist, so this remains open and cannot be settled cheaply. The honest options are to acquire NLDAS-2 on the argument that antecedent wetness is mechanistically central, or to leave the family alone.
- **TerraClimate's accuracy.** Never tested — the audit never needed to read a value.

## Recommendation

1. **Do not acquire TerraClimate.** It is dominated on both of its possible jobs, which is why the verdict is a refusal rather than a deferral:
   - *As the NLDAS gate* — void. Monthly cannot answer a question about days.
   - *As seasonal context* — **gridMET already supplies it.** We hold daily `pr`, `pet` and `etr` at finer resolution over the same region, and any seasonal aggregate TerraClimate offers (how wet was this season against normal, the climatic water deficit) is computable from what is already on disk. The `def` variable is literally `pet − pr` accumulated.
   - The only things TerraClimate has that gridMET does not are **`soil` and `swe`** — genuine state variables rather than fluxes. But if those are wanted, monthly is the wrong cadence and NLDAS-2 (hourly, state variables, Earthdata account already being set up) or SNODAS (daily snow, anonymous) is strictly better.

   The residual case for it is 1950–2025 coverage, which reaches back far earlier than our 2008 water record uses. That is not a reason.
2. **Re-gate NLDAS-2 on cadence, not resolution**, and decide it on the mechanistic argument rather than waiting for a cheap proxy that does not exist. The question is whether *daily-resolved* soil moisture and snow state improve a nitrate model.
3. **Record the geometry result where it will be reused.** It applies to every gridded product the project will ever consider, and it is why the within-basin variance ratio belongs in the thematic audit's standing battery rather than being rediscovered per source.

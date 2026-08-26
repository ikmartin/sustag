Source documentation for the water-treatment-facilities acquisition (chapter: Covariates > Structures).

- hydrowaste-essd-14-559-2022.pdf: Ehalt Macedo et al. 2022, HydroWASTE v1.0 (58,502 WWTPs, georeferenced outfalls, dilution factors) -- the one-time cross-check dataset for our outfall snapping, NOT an authority.
- cwns-2022-detailed-scope-and-methods.pdf: EPA Clean Watersheds Needs Survey 2022 methods -- treatment level / design flow / population-served attributes, joined by NPDES permit number.
- icis-npdes-download-summary.html + npdes-outfalls-summary.html: EPA ECHO download documentation for ICIS-NPDES and the NPDES_OUTFALLS_LAYER.csv (per-outfall NAD83 coordinates, weekly refresh) -- the location authority.
- loading-tool-get-data.html: EPA Water Pollutant Loading Tool download documentation -- DMR-based annual nitrogen loads per facility.

Fetched 2026-08-25. The ECHO pages are HTML because EPA publishes no PDF equivalent.

HydroWASTE_v10.csv itself is NOT archived here yet: figshare's ndownloader answers 202 to non-browser clients persistently, so the 2.3 MB zip needs one manual browser download from hydrosheds.org/products/hydrowaste into this directory; `data/build/scripts/hydrowaste_crosscheck.py` then runs the snapping audit.

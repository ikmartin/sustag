# basin editor (proof of concept)

A modeler's delineation tool over the ACCESS LAYER ONLY -- it never imports the build and stores nothing in the data directories. Basins are node-level objects and nodes are the modeler's domain: what you keep, you keep in your own project files, as a weighted COMID set.

## run

    python tools/basin_editor/app.py            # Dash app on http://127.0.0.1:8062
    python tools/basin_editor/example.py        # the shipped example case (asserts basin1 vs VAA within 1%)

## what it does

Give it a `lat, lon` and it renders basin1 (upstream of the snapped reach), basin2 (upstream of the containing catchment) and basin3 (the HydroSHEDS D8 flood-fill, NHDPlus-independent); give it a USGS site number and it overlays basin0, the authority's own polygon, via one clearly-labeled live NLDI call -- the only network request this tool makes. Each method reports its area beside NHDPlus's own `totdasqkm` so a wrong snap announces itself as a wrong order of magnitude.

The map also shows the SENSORS INSIDE the basin, each with its type -- an arbitrary point has no medium, so the editor never judges; it shows what is there and the modeler reads the situation.

`delineate.to_weighted_set(result)` normalises any method's output to `(comid, weight)` rows -- the representation every access-layer aggregation consumes (`data.access.machinery.aggregate`), and the thing to save in your project.

## the growth path

This skeleton is expected to become a graph-construction / COMID-visualizer tool: basins intersect graph construction, and a tool that shows which sensors, reaches and structures sit inside a drainage is the seed of one that lets you assemble nodes and edges from them.

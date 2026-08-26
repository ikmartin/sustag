"""Per-tab help content for the ? popup: what each queue decides, how the controls work, and one sentence for every field in the merge evidence table.

Prose lives here rather than in `app.py` so the help stays reviewable as text; `for_tab` assembles the popup body for whatever tab is active.
"""

from __future__ import annotations

from dash import html

# One sentence per field of the merge evidence table (the merge sheet's columns, in sheet order). Shown in the merge popup as a glossary.
MERGE_FIELDS = {
    "uid": "The two registrations under review, keyed by source uid; the pair is the unit being decided, and column A is always the map's orange dot and the plot's orange trace, B the purple.",
    "decision": "The verdict cell — `merge` or `distinct` — blank until you decide; blank means not reviewed, never a default.",
    "decided_by": "Free-text attribution for who made the call, written verbatim into the CSV.",
    "note": "Free-text rationale kept beside the verdict — one line on why, for the next reader of the sheet.",
    "criterion_version": "The gate version this row was queued under; a decision made under an older version loses authority and the row resurfaces flagged with its prior verdict.",
    "name": "Each station's declared name from its registry, kept verbatim because the name text is often the best identity evidence (well-nest suffixes, 'DS of' phrasing).",
    "type": "Each side's classified site type (stream, lake, well, ditch, storm sewer, ...) from the D4 type classifier.",
    "nitrate_days": "Days of nitrate record each sensor holds in its canonical store -- the weight of each side's evidence and of what a merge verdict would combine.",
    "first_val": "Date of the first entry in the sensor's timeseries across ANY channel (nitrate, gage, ...), never earlier than the collection window's open in the build config.",
    "last_val": "Date of the last entry in the sensor's timeseries across any channel, capped by the collection window's end if the config ever declares one.",
    "cross_type": "True when the two sides carry different surface types — such pairs are never auto-merged and reach you with the mismatch displayed.",
    "proposed": "The evidence ladder's proposed branch: complementary (no shared discriminating channel), sequential (zero-overlap handoff), dual_registration (agreeing co-recorders), or distinct.",
    "sep_m": "Separation between the STATED coordinates in meters (EPSG:5070); stated, not snapped — the snap residual is context, never identity evidence.",
    "min_sep_m / max_sep_m": "The separation's bounds once each side's declared coordinate precision is honoured (a 2-dp coordinate is a ~700 m box, not a point); the 100 m gate runs on min_sep_m — the closest the two could truly be.",
    "hesitate": "True when min_sep_m exceeds the 50 m hesitation line — every confirmed merge but one in the audit sat under 52 m, so distance alone argues against merging here.",
    "comid": "The NHDPlus reach each side snapped to.",
    "same_comid": "True when both sides snapped to the same reach — same-reach pairs are the usual merge candidates; different reaches make a merge physically suspect.",
    "on_channel": "The discriminating channel the record evidence (shared_days, r, median_diff, agreement) was computed on.",
    "shared_days": "Count of days on which both sides report the evidence channel; correlation is only trusted at 10 or more shared days.",
    "r": "Pearson correlation of the two records over the shared days; the dual_registration branch requires r ≥ 0.80.",
    "median_diff": "Median absolute difference between the two records on shared days, in the channel's native units — small r with small diff reads differently than small r with large diff.",
    "channels": "Each side's full channel set, '+'-joined, with channels PRESENT ON BOTH SENSORS in bold; complementary pairs are exactly those whose discriminating channels do not overlap.",
    "comid_agree": "Per sensor: whether its snapped flowline reach matches the catchment polygon containing it -- a snap-quality diagnostic (False near catchment boundaries), NOT an agreement statistic between the pair.",
    "seam": "For sequential candidates, the handoff gap in days between the end of the earlier record and the start of the later one — the region the plot shades light blue.",
}

_KEYS = html.Ul([
    html.Li([html.Code("j"), " / ", html.Code("k"), " — next / previous row in the queue (focus anywhere outside a text box)"]),
    html.Li([html.Code("1"), "…", html.Code("9"), " — pick the n-th verdict in the radio list"]),
    html.Li([html.Code("u"), " — clear the verdict back to not-reviewed (blank, never a default)"]),
    html.Li([html.Code("Enter"), " — submit: one row is written to the CSV immediately, atomically, mtime-guarded"]),
])

_SHARED = [
    html.H4("Controls (every tab)"),
    _KEYS,
    html.Ul([
        html.Li("The CSVs under data/build/decisions/ are the authority — the panel is a typed pen, nothing is pending in the browser, and closing it loses nothing."),
        html.Li("The FILTER BAR composes: every facet ANDs with the others, and multi-select facets OR within themselves. Status (undecided / decided / decided with a specific verdict on small-vocabulary tabs, e.g. decided (merge) and decided (distinct) / stale / any) and Decider apply on every tab; the merge tab adds Structure (conflicts -- both non-transitive families AND merged pairs whose sides snap to different reaches, the assembly halt's subjects / clusters (3+) / plain pairs), Proposed (the ladder's branches), Site type (matches if EITHER side has a selected type), and the cross-type / hesitate flags. The uid search ANDs with the facets -- to search everything, set status to any. The match count sits beside the row picker; reset returns every facet to its default; filter state persists across reloads (session)."),
        html.Li("After a submit the selection stays on the row if it still matches the filters, and advances to the next matching row when it no longer does (e.g. deciding a row under status=undecided)."),
        html.Li("A row decided under an older criterion version resurfaces flagged with its prior verdict — re-confirm or change it."),
        html.Li("The decision column on the right: verdict radios, any per-ledger literal-value fields (each with its own ? help), decided_by, note, then submit / clear."),
        html.Li("Tab labels carry three lines -- the ledger, decided / total, and the live conflict count (merge counts both non-transitive families and reach-disagreement merges; other tabs have no conflict notion). They refresh after every submit."),
        html.Li("An `exclude` verdict takes effect across the panel immediately: every other tab hides rows referencing the excluded sensor (the build retires them for real on its next pass)."),
    ]),
]

_MAP_LEGEND = html.Ul([
    html.Li("Orange and purple dots — the sensors under review; the time-series traces wear the same colors, dot and trace identify the same sensor."),
    html.Li("Gray dots — the 2 km proximity neighborhood, drawn for context; most are resolved or ineligible pairings, not queue members."),
    html.Li("Rectangles — declared-precision uncertainty of the stated coordinate, per axis; a tall sliver says the latitude has fewer decimals than the longitude."),
    html.Li("Blue reach lines — the snapped NHDPlus reach and its hop-neighborhood; the thin connector from dot to reach is the snap residual (context, never identity evidence)."),
    html.Li("Scale bar bottom-left — the merge gate is 100 m on min_sep_m, so read distances off it."),
])

_FACTS_GLOSSARY = html.Ul([
    html.Li([html.Code("site_uid / station_name / site_type / site_type_source"), " — identity: the registration key, its declared name, the classified type, and which evidence rank assigned it (declared type, name pattern, NHD promotion, human decision)."]),
    html.Li([html.Code("source"), " — the providing network (USGS / IWQIS / MPCA)."]),
    html.Li([html.Code("n_days_nitrate"), " — days of nitrate record in the canonical store."]),
    html.Li([html.Code("lat / lon"), " — the STATED coordinates as the source declared them."]),
    html.Li([html.Code("fetch_status"), " — the acquisition partition state: skipped / cached / source_empty / attempt_failed."]),
    html.Li([html.Code("comid"), " — the NHDPlus reach the sensor snapped to."]),
    html.Li([html.Code("excluded_reason"), " — set when an exclusion verdict has been applied by a build pass."]),
    html.Li([html.Code("channels"), " — every channel with at least one canonical day."]),
])

_TABS = {
    "merge": [
        html.P("Decides whether two registrations within the 100 m gate are one station (merge) or two (distinct). Auto-resolved pairs never reach this queue: what you see passed the material-type gate, is record-bearing on both sides, and was not settled by rule — you are the check the ladder could not replace."),
        html.H4("Evidence on this tab"),
        html.P(["Pairs in a constellation of 3+ sensors lead with the ", html.B("conflict family pane"), ": a map-graph of the whole component at real relative positions (solid green = merged, dashed red = distinct, dotted gray = undecided -- a red edge inside a green blob IS the contradiction), and a clickable decision matrix with a live partition preview showing exactly what d4's consistency check will say. Click any cell to jump to that pair."]),
        html.Ul([
            html.Li("The field table (glossary below) — the pair's full evidence row from the sheet."),
            html.Li("The map:"), _MAP_LEGEND,
            html.Li("The time series — each member's canonical daily record in its dot color; a thick light-gray ghost behind them is the assembled winner-per-span counterfactual (what publishing a merge would produce); a transparent light-blue band marks the seam of a sequential proposal — the handoff a sequential verdict certifies as clean."),
            html.Li("Warnings in amber above the table: cross-type mismatch (never auto-merged) and the 50 m hesitation line."),
            html.Li("Deciding `merge` on a cross-type pair sends every member of the resulting bundle to the site_type queue (merged_mismatch) -- the identity verdict and the type verdict are separate questions, answered in their own tabs."),
            html.Li("Two auto-rules, mutually exclusive by construction, each naming itself in decided_by. Rule 1, merge, 'auto (merge rule 1)': same type, under 10 m, fully disjoint channel sets -- the USGS upgrade pattern, one station re-registered as its instrumentation grows. Rule 2, distinct, 'auto (merge rule 2)': at least one shared channel, spans overlapping >= 50% of the shorter record, at least one shared channel genuinely differing -- the USGS redundancy pattern, twin sensors reporting correlated but different values. All overturnable sheet decisions, inspectable per rule in the 'by decider' view."),
        ]),
        html.H4("The merge table, field by field"),
        html.P(["Two tables: the ", html.B("site comparison table"), " reads ", html.Code("label | A | B"), " with each sensor's values side by side (A is the orange sensor everywhere, B the purple); the ", html.B("pair info table"), " below it holds the pair-level facts -- separation, correlation, the proposal."]),
        html.Table([html.Tr([html.Td(html.Code(k), style={"verticalAlign": "top", "paddingRight": "10px", "whiteSpace": "nowrap"}),
                             html.Td(v)]) for k, v in MERGE_FIELDS.items()],
                   style={"fontSize": 13, "borderSpacing": "0 6px"}),
    ],
    "site_type": [
        html.P("Assigns a site type where the classifier could not: untyped registrations wait as `unknown` rather than defaulting to stream, and cross-surface merge candidates queue here first. The verdict vocabulary is the type list itself."),
        html.Ul([
            html.Li("Evidence: the declared name text (well-nest suffixes, 'LAKE', 'DITCH' phrasing), the source's raw type string, the snapped reach's NHD class (StreamRiver/ArtificialPath → stream, CanalDitch → ditch), and waterbody containment (inside an NHDWaterbody polygon → lake evidence)."),
            html.Li("merged_mismatch rows are members of a human-merged bundle whose types disagree -- your type verdict here is what the bundle's published record will carry."),
            html.Li(["The facts table, row by row:", _FACTS_GLOSSARY]),
            html.Li("Three type auto-rules, keyed on the exact (conflict, kept-type) pair. Rule 1: conflict_tidal kept as stream -> tidal_stream ('auto (type rule 1)') -- the gauge keeps its stream nature but joins the tidal class the basin-modeling exclusions route out. Rule 2: conflict_tidal kept as lake -> lake ('auto (type rule 2)') -- a tidal flag does not make a lake anything else. Rule 3: conflict_tidal kept as well, ditch, wetland_outflow, storm_drain or canal -> the kept type stands ('auto (type rule 3)'). Any other tidal-conflicted type stays a human question. Overturnable like every auto verdict."),
            html.Li("The map draws the sensor with its uncertainty rectangle over the reach and any containing waterbody."),
        ]),
    ],
    "composed": [
        html.P("Confirms proposed multi-member bundles — chains of pairwise merges (typically groundwater well nests) that dissolve into one published record. The only verdict is `confirm`; an unconfirmed bundle stays split and its sensors stay bundle-NULL."),
        html.H4("The summary table, row by row"),
        html.Ul([
            html.Li([html.Code("uid"), " — one column per member; the green ASSEMBLED column is the record confirming produces."]),
            html.Li([html.Code("fronts"), " — YES marks the member whose SNR id fronts the published record; the ASSEMBLED cell repeats its short id (or notes the pick happens at assembly, by highest-tier record days, when not yet marked)."]),
            html.Li([html.Code("name / type"), " — each member's declared name and classified type; ASSEMBLED shows the type set (more than one means a merged_mismatch is heading to the type review)."]),
            html.Li([html.Code("channels"), " — each member's recorded channels; ASSEMBLED is their union — what the composed record carries."]),
            html.Li([html.Code("nitrate_days"), " — per member from its canonical store; ASSEMBLED is the composed record's ACTUAL day count from the live winner-per-span assembly, so member overlap does not double-count."]),
            html.Li([html.Code("first_val / last_val"), " — each member's record span (any channel); ASSEMBLED is the composed record's span."]),
        ]),
        html.P("Composed rule 1 auto-confirms every TWO-member bundle ('auto (composed rule 1)'): the pair decision that built it was made looking at exactly this assembly. Every bundle of 3+ members requires human review — chains are objects transitivity manufactured that no single pair decision approved."),
        html.Ul([
            html.Li("Evidence: every member's record on one timeline (one color each, matching the map dots) and the assembled winner-per-span counterfactual ghosted behind them."),
            html.Li("The map shows the whole constellation with uncertainty rectangles — a nest reads as a tight cluster; a chain that straddles reaches deserves suspicion."),
        ]),
    ],
    "quality": [
        html.P("Decides per-(record, channel) publication masks: `keep` or `exclude`, with two literal-value fields — exclude_spans (date spans masked out) and max_threshold (a ceiling above which values are masked). Cells take literal values in ledger syntax, validated on blur; malformed spans are refused at the form and never written."),
        html.Ul([
            html.Li("Evidence: the daily record with proposed spans shaded orange, the worst segment at native resolution, and the detector signals with per-signal ? help."),
            html.Li("The preview-masks button applies your UNCOMMITTED form values through the real publish-time masking code and shows masked-beside-raw — what the store would publish if you submitted."),
            html.Li("Detector proposals are pre-filled in ledger syntax; edit or clear them — the proposal is evidence, not a default verdict."),
        ]),
    ],
    "drift": [
        html.P("Adjudicates changes to the raw/acquired archive between snapshot cuts. A snapshot is a MANIFEST — per-file content probes (sha256, parquet schema fingerprint, newest data timestamp), ~1.4 MB per cut for the whole 70 GB archive — never a copy, so cuts cost nothing and nothing piles up. The diff between consecutive manifests is classified on content evidence; routine classes (additions, appends, trailing-window refreshes) pass silently, and only the conflicting ones reach this queue."),
        html.Ul([
            html.Li("removed / schema_changed / revised_out_of_window are the conflict classes — each row's evidence pane explains its class and shows the before/after probes plus the file's current on-disk state."),
            html.Li(["The before/after table, row by row: ", html.Code("size"), " — bytes in each cut; ", html.Code("sha256"), " — content hash (first 12 hex chars; differs = the bytes changed); ", html.Code("schema_fp"), " — hash of the parquet column names (differs = the schema changed); ", html.Code("data_max_ts"), " — the newest data timestamp in the file from parquet footer statistics (how a trailing refresh is told apart from a rewrite of old data). '(absent)' means the file is not in that cut's manifest."]),
            html.Li("accept = this state is the legitimate new baseline; the row never resurfaces. Use it for changes the build (or you) made on purpose — a rebuilt layer, a repaired export, a deliberate deletion."),
            html.Li("reject = the change is WRONG — silent history rewrites, unexplained deletions, corruption. Nothing is auto-restored: the verdict keeps the build gated until the file is restored (re-fetch, git, backup) or the verdict changed, so a bad archive state cannot be built on quietly."),
        ]),
    ],
    "registry_gaps": [
        html.P("Acknowledges stations that hold data a registry cannot see (the WQS0127 class): the export has observations, the registry has no row, so no registration is possible. `acknowledged` with a reason keeps the gap on record without silently dropping the station; the fix belongs upstream with the provider."),
    ],
    "exclusions": [
        html.P("Excludes a registration wholesale (`exclude`) or reinstates it (`keep`) — the garbage-test-sensor hatch. An excluded sensor keeps its SNR id and its row in the sensors table with the reason; it just publishes nothing, joins no bundle, and leaves every review queue."),
        html.Li(["The facts table, row by row:", _FACTS_GLOSSARY]),
        html.P("Rows arrive two ways, decisions only one. The build SUGGESTS candidates with the reason in the note (currently: record-bearing sensors with no location), and you can add any uid by hand — type it into the 'uid to exclude' box in the right column and click 'add to sheet' (validated against the sensors table). Either way the row waits blank until you decide it. Exclusions apply at the next d4 pass."),
    ],
}


def for_tab(name: str):
    """The popup body for a tab: its specific help first, the shared controls after."""
    return html.Div(
        [html.H3(f"{name} — help")] + _TABS.get(name, [html.P("No help written for this tab yet.")]) + _SHARED,
        style={"maxWidth": "760px"})

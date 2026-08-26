"""The review panel: one tab per ledger, every decision written to the CSV the moment it is made.

    python -m data.build.review_panel

THE SHEETS STAY THE AUTHORITY. The panel reads the decision CSVs and the build's evidence artifacts and writes single rows through `backend.submit_decision` -- validated, atomic, mtime-guarded. Close the browser mid-review and nothing is lost, because nothing was ever pending in the browser.

KEYBINDINGS (focus anywhere outside a text box): `j`/`k` next/previous row, `1`..`9` pick a verdict, `u` clear the verdict (back to not-reviewed), `Enter` submit. The queue shows UNDECIDED FIRST; a decided row sinks, a criterion-superseded row resurfaces flagged with its prior verdict.
"""

from __future__ import annotations

import json

import pandas as pd
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from dash_extensions import EventListener

from . import backend, plots
from . import help as panel_help
from .tabs import evidence

EVENT = {"event": "keydown", "props": ["key", "target.tagName"]}


def _key_of(name: str, row: dict) -> tuple:
    led = backend.ledgers()[name]
    return tuple(str(row[c]) for c in led.key_cols)


def _row_label(name: str, row: dict) -> str:
    led = backend.ledgers()[name]
    if name == "merge":
        na = str(row.get("name_a") or row.get("uid_a"))
        nb = str(row.get("name_b") or row.get("uid_b"))
        verdict = str(row.get("decision", "")).strip() or "undecided"
        tag = f"{na} : {nb}\u2002|\u2002{verdict}"
        return tag + (f"  [was '{row.get('decision')}' -- reconfirm]" if row.get("_stale") else "")
    tag = " : ".join(str(row[c]) for c in led.key_cols)
    mark = ""
    if row.get("_stale"):
        mark = f"  [was '{row.get('decision')}' -- reconfirm]"
    elif str(row.get("decision", "")).strip():
        mark = f"  [{row.get('decision')}]"
    return tag + mark


def _options(name: str, status: str = "undecided", deciders=None, structure: str = "any",
             proposed=None, sitetypes=None, flags=None, search: str = "") -> list[dict]:
    """The row picker under the FACET FILTERS, all conjunctive: status AND decider AND (on merge) structure/proposed/site-type/flags AND the uid search. Multi-select facets are OR within themselves; empty means any."""
    d = backend.sheet(name)
    if not len(d):
        return []
    dec = d.get("decision", pd.Series("", index=d.index)).astype(str).str.strip()
    if status == "undecided":
        d = d[d["_pending"]]
    elif status == "decided":
        d = d[dec != ""]
    elif status.startswith("decided:"):
        d = d[dec == status.split(":", 1)[1]]
    elif status == "stale":
        d = d[d["_stale"]]
    if deciders:
        d = d[d.get("decided_by", "").astype(str).str.strip().isin(set(deciders))]
    if name == "merge" and len(d):
        if structure == "conflict":
            keys = backend.merge_conflicts() | backend.reach_conflicts()
            d = d[[tuple(k) in keys for k in zip(d.uid_a, d.uid_b)]]
        elif structure == "cluster":
            keys = backend.cluster_keys()
            d = d[[tuple(k) in keys for k in zip(d.uid_a, d.uid_b)]]
        elif structure == "pair":
            keys = backend.cluster_keys()
            d = d[[tuple(k) not in keys for k in zip(d.uid_a, d.uid_b)]]
        if proposed and len(d):
            d = d[d.get("proposed", "").astype(str).str.strip().isin(set(proposed))]
        if sitetypes and len(d):
            sel = set(sitetypes)
            d = d[[str(a) in sel or str(b) in sel for a, b in zip(d.type_a, d.type_b)]]
        for f in (flags or []):
            col = {"cross": "cross_type", "hesitate": "hesitate"}[f]
            if len(d):
                d = d[d.get(col, "").astype(str).isin(("True", "true"))]
    search = str(search or "").strip()
    if search and len(d):
        hit = pd.Series(False, index=d.index)
        for c in ("uid", "site_uid", "uid_a", "uid_b", "members"):
            if c in d.columns:
                hit |= d[c].astype(str).str.contains(search, case=False, regex=False)
        d = d[hit]
    return [{"label": _row_label(name, r), "value": json.dumps(_key_of(name, r))}
            for r in d.to_dict("records")]


def _next_matching(name: str, opts: list[dict], old_key_json: str | None) -> str | None:
    """After a submit: stay on the row if it still matches the filters, else the next matching row in sheet order (wrapping); None when nothing matches."""
    vals = [o["value"] for o in opts]
    if old_key_json in vals:
        return old_key_json
    if not vals:
        return None
    d = backend.sheet(name)
    order = [json.dumps(_key_of(name, r)) for r in d.to_dict("records")]
    at = order.index(old_key_json) if old_key_json in order else -1
    later = [k for k in order[at + 1:] if k in set(vals)]
    return later[0] if later else vals[0]


def _row_for(name: str, key_json: str) -> dict | None:
    d = backend.sheet(name)
    key = tuple(json.loads(key_json))
    led = backend.ledgers()[name]
    for r in d.to_dict("records"):
        if _key_of(name, r) == key:
            return r
    return None


_TAB_STYLE = {"whiteSpace": "pre-line", "lineHeight": "1.3", "fontSize": 13,
              "padding": "5px 14px", "textAlign": "center"}
_TAB_SELECTED = _TAB_STYLE | {"fontWeight": "bold", "borderTop": "2px solid #1f5fa8"}


def _tab_label(name: str) -> str:
    """Three lines: the ledger, decided/total, and the live conflict count (merge's two conflict species; 0 elsewhere)."""
    d = backend.sheet(name)
    total = len(d)
    decided = int((d.decision.astype(str).str.strip() != "").sum()) if total else 0
    conf = len(backend.merge_conflicts() | backend.reach_conflicts()) if name == "merge" else 0
    return f"{name}\n({decided} / {total} decided)\n{conf} conflict{'' if conf == 1 else 's'}"


def _tabs() -> list:
    return [dcc.Tab(label=_tab_label(n), value=n, style=_TAB_STYLE,
                    selected_style=_TAB_SELECTED)
            for n in backend.ledgers()]


def build_app() -> Dash:
    app = Dash(__name__, title="review panel", suppress_callback_exceptions=True)
    counts = backend.queue_counts()
    tabs = _tabs()
    app.layout = EventListener(html.Div([
        html.Button("?", id="help-btn", title="help for this tab",
                    style={"position": "fixed", "top": "12px", "right": "18px", "zIndex": 900,
                           "width": "30px", "height": "30px", "borderRadius": "50%",
                           "fontWeight": "bold", "fontSize": "16px", "cursor": "pointer",
                           "border": "1px solid #999", "background": "#fff"}),
        html.Div(id="help-wrap", style={"display": "none"}, children=[
            html.Div(id="help-close",
                     style={"position": "fixed", "inset": "0", "background": "rgba(0,0,0,0.35)",
                            "zIndex": 1000}),
            html.Div([
                html.Button("×", id="help-x", style={"position": "absolute", "top": "8px",
                                                     "right": "12px", "border": "none",
                                                     "background": "none", "fontSize": "20px",
                                                     "cursor": "pointer"}),
                html.Div(id="help-body"),
            ], style={"position": "fixed", "top": "6vh", "left": "50%",
                      "transform": "translateX(-50%)", "background": "white", "zIndex": 1001,
                      "padding": "18px 26px", "borderRadius": "8px", "maxHeight": "84vh",
                      "overflowY": "auto", "boxShadow": "0 8px 30px rgba(0,0,0,0.3)",
                      "maxWidth": "820px", "width": "90%"}),
        ]),
        html.H3("review panel — the CSVs are the authority; every decision writes immediately"),
        dcc.Tabs(id="tab", value=next(iter(counts)), children=tabs),
        html.Div([
            html.Div([
                dcc.Dropdown(id="f-status", clearable=False, value="undecided",
                             persistence=True, persistence_type="session",
                             style={"width": "165px"}),
                dcc.Dropdown(id="f-decider", multi=True, placeholder="decider (any)",
                             persistence=True, persistence_type="session",
                             style={"minWidth": "180px"}),
                html.Div([
                    dcc.Dropdown(id="f-structure", clearable=False, value="any",
                                 options=[{"label": "any structure", "value": "any"},
                                          {"label": "conflicts", "value": "conflict"},
                                          {"label": "clusters (3+)", "value": "cluster"},
                                          {"label": "plain pairs", "value": "pair"}],
                                 persistence=True, persistence_type="session",
                                 style={"width": "150px"}),
                    dcc.Dropdown(id="f-proposed", multi=True, placeholder="proposed (any)",
                                 persistence=True, persistence_type="session",
                                 style={"minWidth": "170px"}),
                    dcc.Dropdown(id="f-sitetype", multi=True, placeholder="site type (any)",
                                 persistence=True, persistence_type="session",
                                 style={"minWidth": "160px"}),
                    dcc.Checklist(id="f-flags", inline=True,
                                  options=[{"label": " cross-type", "value": "cross"},
                                           {"label": " hesitate", "value": "hesitate"}],
                                  value=[], persistence=True, persistence_type="session",
                                  style={"alignSelf": "center"}),
                ], id="merge-facets", style={"display": "flex", "gap": "8px"}),
                dcc.Input(id="uid-search", placeholder="search by uid", debounce=True,
                          persistence=True, persistence_type="session",
                          style={"width": "220px"}),
                html.Button("reset", id="f-reset", n_clicks=0),
            ], style={"display": "flex", "gap": "8px", "flexWrap": "wrap",
                      "alignItems": "flex-start", "margin": "6px 0"}),
            html.Div([
                html.Div(dcc.Dropdown(id="row", placeholder="pick a row (j/k to step)",
                                      clearable=False), style={"flex": "1"}),
                html.Div(id="match-count",
                         style={"whiteSpace": "nowrap", "alignSelf": "center",
                                "fontFamily": "monospace", "fontSize": 13, "marginLeft": "10px"}),
            ], style={"display": "flex"}),
        ], style={"margin": "8px 0"}),
        html.Div([
            html.Div(id="evidence", style={"flex": "0 0 80%", "minWidth": 0}),
            html.Div([
                html.Div([dcc.Input(id="excl-add-uid", placeholder="uid to exclude (e.g. IWQIS:WQS9953)",
                                    debounce=True, style={"width": "100%", "boxSizing": "border-box"}),
                          html.Button("add to sheet", id="excl-add-btn", n_clicks=0)],
                         id="excl-wrap", style={"display": "none", "flexDirection": "column", "gap": "6px"}),
                dcc.RadioItems(id="decision", inline=False),
                html.Div(id="extras", style={"display": "flex", "flexDirection": "column", "gap": "8px"}),
                dcc.Input(id="decided_by", placeholder="decided_by", debounce=True,
                          style={"width": "100%", "boxSizing": "border-box"}),
                dcc.Input(id="note", placeholder="note", debounce=True,
                          style={"width": "100%", "boxSizing": "border-box"}),
                html.Div([
                    html.Button("submit (Enter)", id="submit", n_clicks=0),
                    html.Button("clear verdict (u)", id="clear", n_clicks=0),
                ], style={"display": "flex", "gap": "10px"}),
                html.Div(id="status", style={"fontFamily": "monospace", "color": "#0a662e",
                                             "fontSize": 12, "overflowWrap": "anywhere"}),
                # BULK ACTIONS, behind a closed disclosure. Every button here writes EVERY row on the
                # sheet in one go; the guard is the point, and "reset" is the undo that makes the
                # other two recoverable.
                html.Details([
                    html.Summary("DANGER", style={"color": "#b91c1c", "fontWeight": "bold",
                                                  "fontSize": 12, "cursor": "pointer"}),
                    html.Div(id="danger-note", style={"fontSize": 11, "color": "#555",
                                                      "margin": "4px 0"}),
                    html.Button("Keep All", id="bulk-keep", n_clicks=0,
                                style={"width": "100%", "marginBottom": "4px"}),
                    html.Button("Exclude All", id="bulk-exclude", n_clicks=0,
                                style={"width": "100%", "marginBottom": "4px"}),
                    html.Button("Reset All Decisions", id="bulk-reset", n_clicks=0,
                                style={"width": "100%"}),
                ], id="danger-wrap", style={"display": "none", "border": "1px solid #fecaca",
                                            "borderRadius": "6px", "padding": "6px 8px",
                                            "background": "#fef2f2", "marginTop": "10px"}),
            ], style={"flex": "0 0 20%", "boxSizing": "border-box", "paddingLeft": "14px",
                      "display": "flex", "flexDirection": "column", "gap": "10px",
                      "alignSelf": "flex-start", "position": "sticky", "top": "8px"}),
        ], style={"display": "flex", "alignItems": "flex-start", "margin": "10px 0"}),
        dcc.Store(id="extras-values", data={}),
        dcc.Store(id="sticky-decided-by", data="", storage_type="session"),
    ], style={"margin": "18px", "fontFamily": "system-ui"}), events=[EVENT], id="keys", logging=False)

    @app.callback(Output("f-decider", "options"), Output("f-proposed", "options"),
                  Output("f-sitetype", "options"), Output("merge-facets", "style"),
                  Output("f-status", "options"), Output("f-status", "value", allow_duplicate=True),
                  Input("tab", "value"), Input("status", "children"), State("f-status", "value"),
                  prevent_initial_call="initial_duplicate")
    def _facet_options(name, _status, cur_status):
        d = backend.sheet(name)

        def uniq(col):
            return (sorted(d.get(col, pd.Series(dtype=str)).astype(str).str.strip()
                           .replace("", pd.NA).dropna().unique()) if len(d) else [])

        deciders = uniq("decided_by")
        st_opts = [{"label": "undecided", "value": "undecided"},
                   {"label": "decided", "value": "decided"}]
        vocab = backend.ledgers()[name].vocabulary
        if len(vocab) <= 4:
            st_opts += [{"label": f"decided ({v})", "value": f"decided:{v}"} for v in vocab]
        st_opts += [{"label": "stale (reconfirm)", "value": "stale"},
                    {"label": "any status", "value": "any"}]
        legal = {o["value"] for o in st_opts}
        st_val = cur_status if cur_status in legal else "undecided"
        if name != "merge":
            return deciders, [], [], {"display": "none"}, st_opts, st_val
        types = sorted(set(uniq("type_a")) | set(uniq("type_b")))
        return (deciders, uniq("proposed"), types, {"display": "flex", "gap": "8px"},
                st_opts, st_val)

    @app.callback(Output("f-status", "value"), Output("f-decider", "value", allow_duplicate=True),
                  Output("f-structure", "value"), Output("f-proposed", "value", allow_duplicate=True),
                  Output("f-sitetype", "value", allow_duplicate=True), Output("f-flags", "value"),
                  Output("uid-search", "value", allow_duplicate=True),
                  Input("f-reset", "n_clicks"), prevent_initial_call=True)
    def _reset(_n):
        return "undecided", [], "any", [], [], [], ""

    @app.callback(Output("danger-wrap", "style"), Output("danger-note", "children"),
                  Input("tab", "value"), Input("status", "children"))
    def _danger_visible(name, _status):
        if name != "quality":
            return {"display": "none"}, ""
        d = backend.sheet(name)
        style = {"display": "block", "border": "1px solid #fecaca", "borderRadius": "6px",
                 "padding": "6px 8px", "background": "#fef2f2", "marginTop": "10px"}
        return style, (f"applies to ALL {len(d)} row(s) on this sheet, at once. Keep/Exclude set the "
                       f"verdict and leave any hand-entered spans/thresholds in place; Reset clears "
                       f"verdicts AND spans AND thresholds.")

    @app.callback(Output("status", "children", allow_duplicate=True),
                  Input("bulk-keep", "n_clicks"), Input("bulk-exclude", "n_clicks"),
                  Input("bulk-reset", "n_clicks"),
                  State("tab", "value"), State("sticky-decided-by", "data"),
                  prevent_initial_call=True)
    def _bulk(_k, _e, _r, name, who):
        if not ctx.triggered or not ctx.triggered[0].get("value"):
            return no_update
        verdict = {"bulk-keep": "keep", "bulk-exclude": "exclude", "bulk-reset": ""}.get(
            ctx.triggered_id, None)
        if verdict is None:
            return no_update
        try:
            return backend.bulk_decide(name, verdict, decided_by=str(who or ""))
        except ValueError as e:
            return f"REFUSED: {e}"

    @app.callback(Output("excl-wrap", "style"), Input("tab", "value"))
    def _excl_visible(name):
        return {"display": "flex", "flexDirection": "column", "gap": "6px"} \
            if name == "exclusions" else {"display": "none"}

    @app.callback(Output("status", "children", allow_duplicate=True),
                  Output("excl-add-uid", "value"),
                  Input("excl-add-btn", "n_clicks"), State("excl-add-uid", "value"),
                  prevent_initial_call=True)
    def _excl_add(_n, uid):
        uid = str(uid or "").strip()
        if not uid:
            return no_update, no_update
        try:
            return backend.open_exclusion(uid), ""
        except ValueError as e:
            return f"REFUSED: {e}", no_update

    @app.callback(Output("tab", "children"), Input("status", "children"),
                  prevent_initial_call=True)
    def _badges(_status):
        return _tabs()

    @app.callback(Output("ts-graph", "figure"), Input("ts-channel", "value"),
                  State("ts-members", "data"), prevent_initial_call=True)
    def _ts_channel(ch, data):
        if not data or not ch:
            return no_update
        cf = backend.merge_counterfactual(data["members"]) if data.get("counterfactual") else None
        return plots.members_figure(data["members"], ch, counterfactual=cf,
                                    seam=bool(data.get("seam")))

    @app.callback(Output("row", "value", allow_duplicate=True),
                  Input({"kind": "fam-jump", "key": ALL}, "n_clicks"),
                  prevent_initial_call=True)
    def _fam_jump(clicks):
        t = ctx.triggered
        if not t or not t[0].get("value"):
            return no_update
        return json.dumps(sorted(json.loads(ctx.triggered_id["key"])))

    @app.callback(Output("help-wrap", "style"), Output("help-body", "children"),
                  Input("help-btn", "n_clicks"), Input("help-close", "n_clicks"),
                  Input("help-x", "n_clicks"), State("tab", "value"), prevent_initial_call=True)
    def _help(n_open, n_close, n_x, tab):
        if ctx.triggered_id == "help-btn":
            return {"display": "block"}, panel_help.for_tab(tab)
        return {"display": "none"}, no_update

    @app.callback(Output("row", "options"), Output("row", "value"),
                  Output("decision", "options"), Output("match-count", "children"),
                  Input("tab", "value"), Input("status", "children"),
                  Input("f-status", "value"), Input("f-decider", "value"),
                  Input("f-structure", "value"), Input("f-proposed", "value"),
                  Input("f-sitetype", "value"), Input("f-flags", "value"),
                  Input("uid-search", "value"), State("row", "value"))
    def _tab(name, _status, f_status, f_dec, f_str, f_prop, f_type, f_flags, search, current):
        opts = _options(name, f_status or "undecided", f_dec, f_str or "any",
                        f_prop, f_type, f_flags, search)
        led = backend.ledgers()[name]
        vocab = [{"label": f" {i + 1}: {v}", "value": v} for i, v in enumerate(led.vocabulary)]
        count = f"{len(opts)} row(s) match"
        if ctx.triggered_id == "status":
            # a submit landed: stay on the row if it still matches the filters, else advance to the
            # next matching row in sheet order
            keep = _next_matching(name, opts, current)
        elif current in {o["value"] for o in opts} and ctx.triggered_id not in ("tab",):
            keep = no_update
        else:
            keep = opts[0]["value"] if opts else None
        return opts, keep, vocab, count

    @app.callback(Output("evidence", "children"), Output("extras", "children"),
                  Output("decision", "value"), Output("decided_by", "value"), Output("note", "value"),
                  Input("row", "value"), State("tab", "value"), State("sticky-decided-by", "data"))
    def _select(key_json, name, sticky):
        if not key_json:
            return html.Div("queue empty"), [], None, str(sticky or ""), ""
        row = _row_for(name, key_json)
        if row is None:
            return html.Div("row not found -- the sheet moved; reload"), [], None, "", ""
        led = backend.ledgers()[name]
        extras = []
        for c in led.extra_editable:
            # LABEL ABOVE, help behind a marked toggle. The label used to live in the input's
            # placeholder with a bare "?" disclosure under it, which browsers draw as an unlabelled
            # collapsible and reads as an empty dropdown.
            # Label, then the hint as plain text, then the input. No disclosure: a one-line hint
            # costs nothing to show and a bare "?" toggle reads as a broken control.
            extras.append(html.Div([
                html.Div(c, style={"fontWeight": "bold", "fontSize": 12}),
                html.Div(backend.EXTRA_HELP.get(c, ""),
                         style={"fontSize": 11, "color": "#555", "lineHeight": "1.35"}),
                dcc.Input(id={"kind": "extra", "field": c}, placeholder=c,
                          value=str(row.get(c, "") or ""), debounce=True,
                          style={"width": "100%", "boxSizing": "border-box"}),
            ], style={"display": "flex", "flexDirection": "column", "gap": "3px"}))
        build = evidence.BUILDERS.get(name, evidence.generic)
        return (build(row), extras,
                str(row.get("decision", "")).strip() or None,
                str(row.get("decided_by", "") or "").strip() or str(sticky or ""),
                str(row.get("note", "") or ""))

    @app.callback(Output("status", "children"), Output("sticky-decided-by", "data"),
                  Input("submit", "n_clicks"), Input("clear", "n_clicks"),
                  State("tab", "value"), State("row", "value"), State("decision", "value"),
                  State("decided_by", "value"), State("note", "value"),
                  State({"kind": "extra", "field": ALL}, "value"),
                  State({"kind": "extra", "field": ALL}, "id"),
                  prevent_initial_call=True)
    def _submit(n_sub, n_clr, name, key_json, decision, decided_by, note, extra_vals, extra_ids):
        if not key_json:
            return "no row selected", no_update
        key = tuple(json.loads(key_json))
        extras = {i["field"]: (v or "") for i, v in zip(extra_ids or [], extra_vals or [])}
        verdict = "" if ctx.triggered_id == "clear" else (decision or "")
        try:
            msg = backend.submit_decision(name, key, verdict,
                                          decided_by=decided_by or "", note=note or "", **extras)
        except ValueError as e:
            return f"REFUSED: {e}", no_update
        return msg, ((decided_by or "").strip() or no_update)

    @app.callback(Output("row", "value", allow_duplicate=True),
                  Output("decision", "value", allow_duplicate=True),
                  Output("submit", "n_clicks", allow_duplicate=True),
                  Output("clear", "n_clicks", allow_duplicate=True),
                  Input("keys", "n_events"), State("keys", "event"),
                  State("row", "options"), State("row", "value"), State("decision", "options"),
                  State("submit", "n_clicks"), State("clear", "n_clicks"),
                  prevent_initial_call=True)
    def _keys(_n, ev, opts, cur, vocab, n_sub, n_clr):
        if not ev or str(ev.get("target.tagName", "")).upper() in ("INPUT", "TEXTAREA"):
            return no_update, no_update, no_update, no_update
        k = str(ev.get("key", ""))
        vals = [o["value"] for o in (opts or [])]
        if k in ("j", "k") and vals:
            i = vals.index(cur) if cur in vals else -1
            i = min(i + 1, len(vals) - 1) if k == "j" else max(i - 1, 0)
            return vals[i], no_update, no_update, no_update
        if k.isdigit() and vocab and 1 <= int(k) <= len(vocab):
            return no_update, vocab[int(k) - 1]["value"], no_update, no_update
        if k == "u":
            return no_update, no_update, no_update, (n_clr or 0) + 1
        if k == "Enter":
            return no_update, no_update, (n_sub or 0) + 1, no_update
        return no_update, no_update, no_update, no_update

    @app.callback(Output("quality-mask-preview", "children"),
                  Input("preview-masks", "n_clicks"),
                  State("tab", "value"), State("row", "value"),
                  State({"kind": "extra", "field": ALL}, "value"),
                  State({"kind": "extra", "field": ALL}, "id"),
                  prevent_initial_call=True)
    def _preview(_n, name, key_json, extra_vals, extra_ids):
        if name != "quality" or not key_json:
            return no_update
        row = _row_for(name, key_json)
        if row is None:
            return "row not found"
        extras = {i["field"]: (v or "") for i, v in zip(extra_ids or [], extra_vals or [])}
        try:
            masked = backend.mask_preview(str(row.get("code")), str(row["channel"]),
                                          extras.get("exclude_spans", ""),
                                          extras.get("max_threshold", ""))
        except ValueError as e:
            return html.Div(f"REFUSED: {e}", style={"color": "#a33"})
        if masked is None:
            return "no assembled record on disk for this code yet"
        raw = pd.read_parquet(backend.config.WATER_MERGED / f"{row.get('code')}.parquet")
        from . import plots

        why = backend.mask_explain(str(row.get("code")), str(row["channel"]),
                                   extras.get("exclude_spans", ""), extras.get("max_threshold", ""))
        lines = [f"raw {why.get('days_total', 0):,} day(s) -> published {len(masked):,}"]
        if "days_in_spans" in why:
            lines.append(f"{why['days_in_spans']} day(s) inside the excluded spans")
        if "threshold" in why:
            lines.append(f"ceiling {why['threshold']:g}: {why['days_over']} day(s) withheld WHOLE, "
                         f"of which {why.get('days_over_whose_mean_is_under', 0)} have a mean UNDER "
                         f"the ceiling -- a day goes when ANY of its statistics exceeds it, because "
                         f"the mean was computed from the offending samples too")
            trips = why.get("tripped_by") or {}
            if trips:
                lines.append("tripped by: " + ", ".join(f"{k.split('_', 1)[1]} ({v})"
                                                        for k, v in sorted(trips.items(),
                                                                           key=lambda kv: -kv[1])))
        return html.Div([
            html.Div([html.Div(x, style={"marginBottom": "2px"}) for x in lines],
                     style={"fontFamily": "monospace", "fontSize": 12, "background": "#f8fafc",
                            "border": "1px solid #e2e8f0", "borderRadius": "6px",
                            "padding": "8px 10px", "margin": "6px 0", "maxWidth": "900px"}),
            dcc.Graph(figure=plots.masked_figure(raw, masked, str(row["channel"]))),
        ])

    return app


def main():
    app = build_app()
    app.run(debug=False, port=8061)


if __name__ == "__main__":
    main()

"""Per-ledger evidence builders: given one sheet row, assemble what a reviewer needs to see.

One function per ledger, all returning a list of Dash components. The generic tab engine in `app` knows nothing about any ledger's semantics; everything ledger-specific is here.
"""

from __future__ import annotations

import os

import pandas as pd
from dash import dash_table, dcc, html

from .. import backend, plots
from .. import map as panel_map
from ... import config


A_HDR, B_HDR = "#f97316", "#7c3aed"      # the map/plot palette: A is always orange, B purple


def _pair_table(row: dict, sensors: "pd.DataFrame"):
    """label | A | B evidence table for a merge pair: per-sensor fields side by side (uid, name, type, nitrate_days, first_val/last_val, comid, channels, agree), pair-level fields spanning both columns."""
    from .. import backend

    a, b = row["uid_a"], row["uid_b"]
    srow = {r.site_uid: r for r in sensors.itertuples()} if sensors is not None else {}
    span = {u: backend.record_span(u) for u in (a, b)}

    def cell(v):
        if isinstance(v, list):
            return v
        s = str(v)
        return "" if s in ("", "nan", "<NA>", "None", "NaT") else s

    def chanset(v):
        return {c for c in str(v or "").split("+") if c and c not in ("nan", "<NA>")}

    shared_ch = chanset(row.get("channels_a")) & chanset(row.get("channels_b"))

    def chan_cell(v):
        # shared channels in bold: the overlap is what makes a channel DISCRIMINATING for the pair
        parts = []
        for c in sorted(chanset(v)):
            parts += [html.B(c) if c in shared_ch else c, "+"]
        return parts[:-1] if parts else ""

    paired = [("uid", a, b),
              ("name", row.get("name_a"), row.get("name_b")),
              ("type", row.get("type_a"), row.get("type_b")),
              ("nitrate_days", getattr(srow.get(a), "n_days_nitrate", ""), getattr(srow.get(b), "n_days_nitrate", "")),
              ("first_val", span[a][0], span[b][0]),
              ("last_val", span[a][1], span[b][1]),
              ("comid", row.get("comid_a"), row.get("comid_b")),
              ("channels", chan_cell(row.get("channels_a")), chan_cell(row.get("channels_b"))),
              ("comid_agree", row.get("agree_a"), row.get("agree_b"))]
    used = {"Row", "uid_a", "uid_b", "name_a", "name_b", "type_a", "type_b", "comid_a", "comid_b",
            "channels_a", "channels_b", "agree_a", "agree_b"}
    bordered = {"border": "1px solid #ccc", "padding": "5px 9px", "overflowWrap": "anywhere"}
    label_td = bordered | {"fontWeight": "bold"}
    table_style = {"fontSize": 12, "fontFamily": "monospace", "borderCollapse": "collapse",
                   "width": "100%", "tableLayout": "fixed"}
    caption = {"fontWeight": "bold", "fontSize": 12, "margin": "8px 0 4px"}

    site_trs = [html.Tr([html.Th("", style=bordered | {"width": "22%"}),
                         html.Th("Sensor A", style=bordered | {"color": A_HDR, "width": "39%"}),
                         html.Th("Sensor B", style=bordered | {"color": B_HDR, "width": "39%"})])]
    for label, va, vb in paired:
        site_trs.append(html.Tr([html.Td(label, style=label_td),
                                 html.Td(cell(va), style=bordered),
                                 html.Td(cell(vb), style=bordered)]))
    pair_trs = [html.Tr([html.Td(k, style=label_td), html.Td(cell(v), style=bordered)])
                for k, v in row.items()
                if k not in used and not k.startswith("_") and cell(v) != ""]
    return html.Div([
        html.Div("site comparison table", style=caption),
        html.Table(site_trs, style=table_style),
        html.Div("pair info table", style=caption),
        html.Table(pair_trs, style=table_style),
    ], style={"maxHeight": "420px", "overflowY": "auto"})


def _kv_table(row: dict, skip=("Row",)):
    items = [(k, str(v)) for k, v in row.items()
             if k not in skip and not k.startswith("_") and str(v) not in ("", "nan", "<NA>", "None")]
    return dash_table.DataTable(
        data=[dict(field=k, value=v) for k, v in items],
        columns=[{"name": "field", "id": "field"}, {"name": "value", "id": "value"}],
        style_cell={"textAlign": "left", "fontSize": 12, "fontFamily": "monospace",
                    "whiteSpace": "normal", "height": "auto"},
        style_header={"display": "none"}, style_table={"maxHeight": "380px", "overflowY": "auto"})


def _safe(build):
    try:
        return build()
    except Exception as e:                                   # noqa: BLE001 -- evidence must not sink the tab
        return [html.Div(f"evidence unavailable: {type(e).__name__}: {e}",
                         style={"color": "#a33", "fontFamily": "monospace"})]


def _who_initial(w: str) -> str:
    w = str(w or "")
    if w.startswith("auto (merge rule"):
        return "a" + w.rstrip(")").split()[-1]
    return w[:1] or ""


def _family_section(row: dict) -> list:
    import json as _json

    fam = backend.conflict_family(row["uid_a"], row["uid_b"])
    if fam is None:
        return []
    members = fam["members"]
    verdicts = {tuple(sorted((e["a"], e["b"]))): e for e in fam["edges"]}
    bg = {"merge": "#dcfce7", "distinct": "#fee2e2", "undecided": "#f3f4f6"}
    mark = {"merge": "M", "distinct": "D", "undecided": "·"}
    short = {m: m.split(":")[-1][-4:] for m in members}
    head = html.Tr([html.Th("")] + [html.Th(short[m], style={"padding": "2px 6px"}) for m in members])
    trs = [head]
    for a in members:
        cells = [html.Td(short[a], style={"fontWeight": "bold", "padding": "2px 6px"})]
        for b in members:
            if a == b:
                cells.append(html.Td("", style={"background": "#e5e7eb"}))
                continue
            e = verdicts.get(tuple(sorted((a, b))))
            if e is None:
                cells.append(html.Td("", title="no pair row (beyond gate or record-less)"))
                continue
            label = f"{mark[e['verdict']]}{_who_initial(e['decided_by'])}"
            cells.append(html.Td(html.Button(
                label, id={"kind": "fam-jump", "key": _json.dumps(sorted((e["a"], e["b"])))},
                n_clicks=0, title=f"{e['a']} : {e['b']} -- {e['verdict']} ({e['decided_by'] or 'undecided'})",
                style={"border": "none", "background": "none", "cursor": "pointer",
                       "fontFamily": "monospace", "fontSize": 12, "width": "100%"}),
                style={"background": bg[e["verdict"]], "border": "1px solid #ccc",
                       "textAlign": "center", "minWidth": "34px"}))
        trs.append(html.Tr(cells))
    parts_txt = "  |  ".join("{" + " + ".join(short[m] for m in p) + "}" for p in fam["parts"])
    if fam["violations"]:
        verdict_line = html.Div([html.B("INCONSISTENT: "),
                                 f"current merges imply {parts_txt}, violating "
                                 f"{len(fam['violations'])} distinct ruling(s): " +
                                 ", ".join(f"{short[a]}-{short[b]}" for a, b in fam["violations"])],
                                style={"color": "#b91c1c", "marginTop": "6px"})
    else:
        verdict_line = html.Div([html.B("consistent: "), f"current merges imply {parts_txt}"],
                                style={"color": "#166534", "marginTop": "6px"})
    return [html.Div([
        html.Div(f"conflict family -- {len(members)} sensors, {len(fam['edges'])} pair rows "
                 f"(cells jump to the pair; M=merge, D=distinct, i/a1/a2=decider; "
                 f"co-located sensors are fanned apart for legibility)",
                 style={"fontWeight": "bold", "margin": "8px 0 4px"}),
        html.Div([
            html.Div(dcc.Graph(figure=plots.family_figure(fam).to_plotly_json()),
                     style={"flex": "0 0 55%", "minWidth": 0}),
            html.Div([html.Table(trs, style={"fontSize": 12, "fontFamily": "monospace",
                                             "borderCollapse": "collapse"}),
                      verdict_line],
                     style={"flex": "0 0 45%", "boxSizing": "border-box", "paddingLeft": "12px"}),
        ], style={"display": "flex", "alignItems": "flex-start"}),
    ])]


def merge(row: dict) -> list:
    def build():
        a, b = row["uid_a"], row["uid_b"]
        ch = row.get("on_channel") or "nitrate"
        ch = ch if str(ch) not in ("", "nan", "<NA>") else "nitrate"
        members = [a, b]
        cf = None
        if row.get("proposed") in ("complementary", "sequential", "dual_registration"):
            cf = backend.merge_counterfactual(members)
        rows = backend.sensor_rows(members)
        out = []
        out += _family_section(row)
        if row.get("cross_type") in ("True", True):
            out.append(html.Div(f"TYPE MISMATCH: {row.get('type_a')} vs {row.get('type_b')} -- "
                                f"never auto-merged; you are the check on a noisy label",
                                style={"color": "#b45309", "fontWeight": "bold"}))
        if row.get("hesitate") in ("True", True):
            out.append(html.Div(f"stated separation {row.get('sep_m')} m -- beyond the 50 m hesitation "
                                f"line; every confirmed merge but one sits under 52 m",
                                style={"color": "#b45309", "fontWeight": "bold"}))
        seam = row.get("proposed") == "sequential"
        # ONLY channels one of the two sensors actually declares -- no injected defaults
        ts_channels = sorted(({c for c in str(row.get("channels_a") or "").split("+")} |
                              {c for c in str(row.get("channels_b") or "").split("+")}) -
                             {"", "nan", "<NA>"})
        ch = ch if ch in ts_channels else (ts_channels[0] if ts_channels else None)
        out += [
            html.Div([html.Div(_pair_table(row, rows),
                               style={"flex": "0 0 50%", "minWidth": 0, "boxSizing": "border-box",
                                      "paddingRight": "8px"}),
                      html.Div(panel_map.pair_map(rows, backend.proximity_neighbors(members)),
                               style={"flex": "0 0 50%", "minWidth": 0, "boxSizing": "border-box",
                                      "paddingLeft": "8px"})],
                     style={"display": "flex"}),
            html.Div([
                dcc.Store(id="ts-members", data=dict(members=members, seam=seam,
                                                     counterfactual=cf is not None)),
                html.Div(dcc.Dropdown(id="ts-channel", value=ch, clearable=False,
                                      options=[{"label": c, "value": c} for c in ts_channels],
                                      style={"width": "190px", "fontSize": 12}),
                         style={"position": "absolute", "top": "2px", "right": "16px", "zIndex": 10}),
                dcc.Graph(id="ts-graph",
                          figure=plots.members_figure(members, ch, counterfactual=cf, seam=seam)),
            ], style={"position": "relative"}),
        ]
        return out
    return _safe(build)


def _composed_summary(members: list[str], rows, cf) -> "html.Div":
    """label | member1 | ... | memberN | ASSEMBLED: each sensor separately, then what confirming composes -- fronting member, union of channels, union span, and per-day counts the assembly actually achieves."""
    import pandas as pd

    facts, spans, chansets = {}, {}, {}
    for m in members:
        r = rows.loc[m] if m in rows.index else None
        spans[m] = backend.record_span(m)
        chansets[m] = {c[len("n_days_"):] for c in rows.columns
                       if c.startswith("n_days_") and r is not None and (r[c] or 0) > 0}
        facts[m] = r
    front = next((m for m in members if facts[m] is not None
                  and bool(pd.notna(facts[m].get("is_bundle_canonical"))
                           and facts[m].get("is_bundle_canonical"))), None)
    asm_days = ""
    asm_span = ("", "")
    if cf is not None and len(cf) and "nitrate_mean" in cf.columns:
        asm_days = int(cf.nitrate_mean.notna().sum())
        dates = pd.to_datetime(cf.date)
        asm_span = (str(dates.min().date()), str(dates.max().date()))
    bordered = {"border": "1px solid #ccc", "padding": "4px 8px", "overflowWrap": "anywhere"}
    asm_style = bordered | {"background": "#f0fdf4", "fontWeight": "bold"}
    label_td = bordered | {"fontWeight": "bold", "whiteSpace": "nowrap"}

    def get(m, field):
        r = facts[m]
        if r is None:
            return ""
        v = r.get(field)
        return "" if v is None or (isinstance(v, float) and pd.isna(v)) or str(v) in ("nan", "<NA>") else str(v)

    def days(m):
        r = facts[m]
        return "" if r is None or pd.isna(r.get("n_days_nitrate")) else str(int(r["n_days_nitrate"]))

    # header labels: strip the LONGEST COMMON PREFIX of the native ids (nest members differ only in
    # a short tail) and mark the cut with an ellipsis; mixed-source families keep the source prefix
    natives = [m.split(":")[-1] for m in members]
    common = os.path.commonprefix(natives) if len(set(m.split(":")[0] for m in members)) == 1 else ""
    cut = len(common) if len(common) >= 4 else 0

    def head_label(m):
        nat = m.split(":")[-1]
        tail = nat[cut:] or nat[-4:]
        return ("\u2026" + tail) if cut else (m if len(m) <= 14 else m.split(":")[0] + ":\u2026" + nat[-6:])

    rows_spec = [
        ("uid", lambda m: m, "one published record"),
        ("fronts", lambda m: "YES" if m == front else "",
         head_label(front) if front else "picked at assembly (highest-tier days)"),
        ("name", lambda m: get(m, "station_name"), ""),
        ("type", lambda m: get(m, "site_type"), " / ".join(sorted({get(m, "site_type") for m in members} - {""}))),
        ("channels", lambda m: "+".join(sorted(chansets[m])), "+".join(sorted(set().union(*chansets.values())))),
        ("nitrate_days", days, str(asm_days)),
        ("first_val", lambda m: spans[m][0] or "", asm_span[0]),
        ("last_val", lambda m: spans[m][1] or "", asm_span[1]),
    ]
    trs = [html.Tr([html.Th("", style=bordered)] +
                   [html.Th(head_label(m), style=bordered, title=m) for m in members] +
                   [html.Th("ASSEMBLED", style=asm_style)])]
    for label, fn, asm in rows_spec:
        trs.append(html.Tr([html.Td(label, style=label_td)] +
                           [html.Td(fn(m), style=bordered) for m in members] +
                           [html.Td(asm, style=asm_style)]))
    return html.Div(html.Table(trs, style={"fontSize": 12, "fontFamily": "monospace",
                                           "borderCollapse": "collapse"}),
                    style={"overflowX": "auto", "margin": "8px 0"})


def composed(row: dict) -> list:
    def build():
        members = str(row["members"]).split("+")
        cf = backend.merge_counterfactual(members)
        rows = backend.sensor_rows(members)
        return [
            html.Div("Confirm the COMPOSED record your pairwise answers built. Rejecting it means "
                     "editing the pairs in merge.csv; membership is the key, so the new composition "
                     "re-queues on its own row.", style={"fontStyle": "italic"}),
            _composed_summary(members, rows, cf),
            html.Div([html.Div(_kv_table(row), style={"flex": "1"}),
                      html.Div(panel_map.pair_map(rows, backend.proximity_neighbors(members)),
                               style={"flex": "1.4", "marginLeft": "10px"})],
                     style={"display": "flex"}),
            dcc.Graph(figure=plots.members_figure(members, "nitrate", counterfactual=cf)),
        ]
    return _safe(build)


def _single_sensor_view(uid: str, header: str) -> list:
    """The one-sensor evidence layout shared by site_type and exclusions: header line, sensors-table facts beside the map, record graph with the channel dropdown."""
    rows = backend.sensor_rows([uid])
    srow = rows.iloc[0] if len(rows) else None
    chans = sorted(c[len("n_days_"):] for c in rows.columns
                   if c.startswith("n_days_") and srow is not None
                   and (srow[c] or 0) > 0) if srow is not None else []
    ch = "nitrate" if "nitrate" in chans else (chans[0] if chans else None)
    graph = html.Div("no record on disk")
    if ch is not None:
        graph = html.Div([
            dcc.Store(id="ts-members", data=dict(members=[uid], seam=False, counterfactual=False)),
            html.Div(dcc.Dropdown(id="ts-channel", value=ch, clearable=False,
                                  options=[{"label": c, "value": c} for c in chans],
                                  style={"width": "190px", "fontSize": 12}),
                     style={"position": "absolute", "top": "2px", "right": "16px", "zIndex": 10}),
            dcc.Graph(id="ts-graph", figure=plots.members_figure([uid], ch)),
        ], style={"position": "relative"})
    facts = {k: srow[k] for k in ("site_uid", "station_name", "site_type", "site_type_source",
                                  "source", "n_days_nitrate", "lat", "lon", "fetch_status",
                                  "comid", "excluded_reason")
             if srow is not None and k in rows.columns} if srow is not None else {"site_uid": uid}
    facts["channels"] = "+".join(chans) if chans else "(none)"
    return [
        html.Div(header, style={"fontWeight": "bold"}),
        html.Div([html.Div(_kv_table(facts), style={"flex": "1"}),
                  html.Div(panel_map.pair_map(rows), style={"flex": "1.4", "marginLeft": "10px"})],
                 style={"display": "flex"}),
        graph,
    ]


def exclusions(row: dict) -> list:
    def build():
        uid = row["uid"]
        return _single_sensor_view(uid, f"exclude or keep {uid}? -- current note: {row.get('note') or '(none)'}")
    return _safe(build)


_DRIFT_CLASS_HELP = {
    "removed": "The file is in the parent manifest but not the child's: deleted, renamed, or newly excluded from manifests. Accept if the removal was intentional; reject if nothing should have removed it.",
    "schema_changed": "The parquet column set changed between cuts -- either the producer changed its schema deliberately, or something rewrote the file wholesale. Accept a deliberate schema change; reject an unexplained one.",
    "revised_out_of_window": "Content changed in data OLDER than the source's declared revision window -- approved history moved, which no routine refresh explains. Accept a known cause (a repaired export, a deliberate rebuild); reject silent history rewrites.",
    "added": "New file since the parent cut.",
    "extended": "The file grew and its newest data advanced -- an append.",
    "revised_in_window": "A trailing-window refresh -- the source revising recent data as declared.",
}


def drift(row: dict) -> list:
    def build():
        pair = str(row["snapshot_pair"])
        parent_id, child_id = pair.split("..")
        relpath = str(row["diff_id"])
        cls = str(row.get("class", ""))
        sides = []
        for label, sid in (("before", parent_id), ("after", child_id)):
            p = config.SNAPSHOTS / sid / "manifest.parquet"
            hit = None
            if p.exists():
                m = pd.read_parquet(p)
                h = m[m.relpath == relpath]
                hit = h.iloc[0] if len(h) else None
            sides.append((label, sid, hit))
        fields = ("size", "sha256", "schema_fp", "data_max_ts")

        def cell(hit, f):
            if hit is None:
                return "(absent)"
            v = hit.get(f)
            s = str(v)
            if s in ("None", "nan", "NaT", "<NA>"):
                return ""
            return s[:12] if f in ("sha256", "schema_fp") else s

        trs = [html.Tr([html.Th("")] + [html.Th(f"{lb} ({sid})") for lb, sid, _ in sides])]
        for f in fields:
            trs.append(html.Tr([html.Td(f, style={"fontWeight": "bold"})] +
                               [html.Td(cell(hit, f)) for _, _, hit in sides]))
        live = config.ACQUIRED.parent / relpath      # role-prefixed relpath; survives the store migration
        return [
            html.Div(relpath, style={"fontWeight": "bold", "fontFamily": "monospace"}),
            html.Div(f"class: {cls} -- {_DRIFT_CLASS_HELP.get(cls, '')}",
                     style={"margin": "6px 0", "maxWidth": "900px"}),
            html.Table(trs, style={"fontSize": 12, "fontFamily": "monospace",
                                   "borderSpacing": "12px 3px"}),
            html.Div(f"on disk now: {'%s bytes' % f'{live.stat().st_size:,}' if live.exists() else 'ABSENT'}",
                     style={"fontSize": 12, "marginTop": "6px"}),
            html.Div("accept = this state is the new baseline; the row never resurfaces. reject = the change is wrong -- the build keeps gating here until the file is restored or the verdict changed; nothing is auto-restored.",
                     style={"fontSize": 12, "marginTop": "8px", "color": "#555"}),
        ]
    return _safe(build)


def site_type(row: dict) -> list:
    def build():
        uid = row["site_uid"]
        rows = backend.sensor_rows([uid])
        srow = rows.iloc[0] if len(rows) else None
        chans = sorted(c[len("n_days_"):] for c in rows.columns
                       if c.startswith("n_days_") and srow is not None
                       and (srow[c] or 0) > 0) if srow is not None else []
        ch = "nitrate" if "nitrate" in chans else (chans[0] if chans else None)
        graph = html.Div("no record on disk")
        if ch is not None:
            graph = html.Div([
                dcc.Store(id="ts-members", data=dict(members=[uid], seam=False,
                                                     counterfactual=False)),
                html.Div(dcc.Dropdown(id="ts-channel", value=ch, clearable=False,
                                      options=[{"label": c, "value": c} for c in chans],
                                      style={"width": "190px", "fontSize": 12}),
                         style={"position": "absolute", "top": "2px", "right": "16px",
                                "zIndex": 10}),
                dcc.Graph(id="ts-graph", figure=plots.members_figure([uid], ch)),
            ], style={"position": "relative"})
        kv = {**row, "channels": "+".join(chans) if chans else "(none)"}
        return [
            html.Div(str(row.get("question", "")), style={"fontWeight": "bold"}),
            html.Div([html.Div(_kv_table(kv), style={"flex": "1"}),
                      html.Div(panel_map.pair_map(rows), style={"flex": "1.4", "marginLeft": "10px"})],
                     style={"display": "flex"}),
            graph,
        ]
    return _safe(build)


def _spans_text(spans: str) -> str:
    """The proposed spans one per line, in the ledger's own `A..B` syntax, ready to paste.

    The sheet stores them `;`-joined on one line, which is what `parse_spans` reads back, and which is unreadable at thirty spans. One per line is legal input too -- the parser splits on `;` and strips whitespace -- so a reviewer can delete the lines they disagree with and paste the rest.
    """
    parts = [p.strip() for p in str(spans or "").split(";") if p.strip()]
    return ";\n".join(parts)


def _spans_note(spans: str) -> str:
    """How many spans and how many days they cover -- the size of what is being proposed."""
    import datetime as _dt

    parts = [p.strip() for p in str(spans or "").split(";") if p.strip()]
    days = 0
    for p in parts:
        try:
            a, b = p.split("..")
            days += (_dt.date.fromisoformat(b.strip()) - _dt.date.fromisoformat(a.strip())).days + 1
        except Exception:                                      # noqa: BLE001 -- a malformed span is the form's problem
            continue
    return f"{len(parts)} span(s), {days} day(s) proposed for exclusion" if parts else "no spans proposed"


def quality(row: dict) -> list:
    def build():
        members = str(row["members"]).split("+")
        ch = str(row["channel"])
        series = backend.native_series(members, ch)
        signals = [s.strip() for s in str(row.get("reason", "")).split(",") if s.strip() in backend.SIGNAL_HELP]
        # WHY THE ROW IS FLAGGED IS THE POINT OF THE TAB, so it is shown, not hidden behind a
        # disclosure. These used to be collapsed `<details>` whose only visible text was "?", which
        # is unreadable: a reviewer cannot tell a clickable help toggle from a broken control.
        helps = [html.Div([
            html.Div(f"{sig}", style={"fontWeight": "bold", "fontFamily": "monospace",
                                      "fontSize": 12, "color": "#b45309"}),
            html.Div(backend.SIGNAL_HELP[sig], style={"fontSize": 12, "color": "#333"}),
        ], style={"margin": "0 0 6px"}) for sig in signals]
        helps = [html.Div(helps, style={"border": "1px solid #e5e7eb", "borderRadius": "6px",
                                        "padding": "8px 10px", "background": "#fffbeb",
                                        "maxWidth": "900px", "margin": "6px 0"})] if helps else []
        spans = str(row.get("proposed_spans", "") or "")
        rows = backend.sensor_rows(members)
        return [
            html.Div(f"signals: {row.get('reason')}   score {row.get('score')}",
                     style={"fontFamily": "monospace"}),
            *helps,
            # THE PLOT LEADS. The spans and the map are what a reviewer consults AFTER seeing the
            # shape of the record, so they sit below it rather than pushing it off the screen.
            dcc.Graph(figure=plots.record_figure(series, spans,
                                                 title=f"{row.get('code')} {ch}")),
            html.Div([
                html.Div([
                    html.Div("proposed spans -- select and copy into exclude_spans",
                             style={"fontWeight": "bold", "fontSize": 12, "margin": "6px 0 3px"}),
                    # a TEXTAREA, not a div: the reviewer's job here is to copy some or all of these
                    # into the decision cell, and text in a div fights the selection
                    dcc.Textarea(value=_spans_text(spans), readOnly=True, id="quality-spans-text",
                                 style={"width": "100%", "height": "300px", "fontFamily": "monospace",
                                        "fontSize": 12, "boxSizing": "border-box"}),
                    html.Div(_spans_note(spans), style={"fontSize": 11, "color": "#555",
                                                        "marginTop": "3px"}),
                ], style={"flex": "0 0 50%", "minWidth": 0, "boxSizing": "border-box",
                          "paddingRight": "8px"}),
                html.Div(panel_map.pair_map(rows, backend.proximity_neighbors(members)),
                         style={"flex": "0 0 50%", "minWidth": 0, "boxSizing": "border-box",
                                "paddingLeft": "8px"}),
            ], style={"display": "flex", "alignItems": "flex-start"}),
            html.Button("preview masks (uncommitted form values)", id="preview-masks", n_clicks=0),
            html.Div(id="quality-mask-preview"),
        ]
    return _safe(build)


def generic(row: dict) -> list:
    return [_kv_table(row)]


BUILDERS = {"merge": merge, "composed": composed, "site_type": site_type,
            "quality": quality, "drift": drift, "registry_gaps": generic, "exclusions": exclusions}

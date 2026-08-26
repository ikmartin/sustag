"""Plotly figure builders for the review panel -- every plot hoverable, dates readable off the cursor.

Span decisions are written as LITERAL DATES, so the record plot's first job is to let a reviewer read a date. Daily resolution for full records (the day is the unit being chosen; it also keeps a 700k-sample series at a few thousand points), native resolution for the worst segment (sampling-interval behaviour is what it shows).
"""

from __future__ import annotations

import pandas as pd

A_COLOR, B_COLOR = "#f97316", "#7c3aed"
MEMBER_COLORS = (A_COLOR, B_COLOR, "#2ca02c", "#b8860b", "#d62728")
INK_2 = "#555"


def record_figure(series: pd.DataFrame, proposed_spans: str = "", title: str = ""):
    """One (record, channel) native series: daily mean with hover readout, proposal windows shaded, worst segment beside."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    from ..publish import quality

    fig = make_subplots(rows=1, cols=2, column_widths=[0.68, 0.32], horizontal_spacing=0.05,
                        subplot_titles=[title or "record (daily)", "worst segment (native)"])
    v = series.dropna(subset=["value"]) if len(series) else series
    if len(v):
        day = (v.set_index(pd.DatetimeIndex(v.datetime)).value
               .resample("D").agg(["mean", "min", "max", "count"]).dropna(subset=["mean"]))
        fig.add_trace(go.Scatter(
            x=day.index, y=day["mean"], mode="lines", line=dict(width=0.9, color=A_COLOR),
            customdata=day[["min", "max", "count"]].to_numpy(),
            hovertemplate=("%{x|%Y-%m-%d}<br>mean %{y:.3f}<br>min %{customdata[0]:.3f}"
                           "  max %{customdata[1]:.3f}<br>%{customdata[2]:.0f} sample(s)<extra></extra>"),
            showlegend=False), row=1, col=1)
        for a, b in quality.parse_spans(proposed_spans or ""):
            fig.add_vrect(x0=a, x1=b + pd.Timedelta(days=1), row=1, col=1,
                          fillcolor="#e08214", opacity=0.18, line_width=0)
        from ..publish.quality import SEG_W

        seg = _worst_segment(v, SEG_W)
        if len(seg):
            fig.add_trace(go.Scattergl(
                x=seg.datetime, y=seg.value, mode="lines", line=dict(width=0.7, color=INK_2),
                hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.3f}<extra></extra>",
                showlegend=False), row=1, col=2)
    fig.update_layout(height=340, template="plotly_white", margin=dict(t=40, l=45, r=15, b=30))
    return fig


def _worst_segment(d: pd.DataFrame, seg_w: int) -> pd.DataFrame:
    """The most anomalous SEG_W-sample window: highest diff-to-std ratio, falling back to the flattest."""
    import numpy as np

    v = d.value.to_numpy(dtype="float64")
    if len(v) <= seg_w:
        return d
    nseg = len(v) // seg_w
    vt = v[: nseg * seg_w].reshape(nseg, seg_w)
    wstd = vt.std(axis=1)
    dstd = np.diff(vt, axis=1).std(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        score = np.where(wstd > 0, dstd / np.where(wstd > 0, wstd, 1), np.inf)
    k = int(np.nanargmax(score))
    return d.iloc[k * seg_w:(k + 1) * seg_w]


def members_figure(members: list[str], channel: str = "nitrate", counterfactual: pd.DataFrame | None = None, seam: bool = False):
    """Every member's canonical daily series on one timeline, one colour each, the assembled record shown ghosted behind them when handed one -- the composed-record counterfactual. Trace i wears MEMBER_COLORS[i], the same palette the map dots draw from, so the plot and the map identify a sensor by one colour. `seam=True` (the sequential proposal) shades the junction between the two records light blue -- the region where one record hands off to the other is exactly what a sequential verdict asserts is clean."""
    import plotly.graph_objects as go

    from . import backend

    fig = go.Figure()
    if counterfactual is not None and len(counterfactual) and f"{channel}_mean" in counterfactual.columns:
        c = counterfactual.dropna(subset=[f"{channel}_mean"])
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(c.date), y=c[f"{channel}_mean"], mode="lines",
            line=dict(width=5, color="#dddddd"), name="assembled (winner per span)",
            hovertemplate="%{x|%Y-%m-%d}<br>assembled %{y:.3f}<extra></extra>"))
    loaded = {}
    for i, u in enumerate(members):
        s = backend.daily_record(u, channel)
        if s is None:
            continue
        loaded[u] = s
        fig.add_trace(go.Scatter(
            x=s.index, y=s.to_numpy(), mode="lines",
            line=dict(width=1.0, color=MEMBER_COLORS[i % len(MEMBER_COLORS)]), name=u,
            hovertemplate=f"{u}<br>%{{x|%Y-%m-%d}}<br>%{{y:.3f}}<extra></extra>"))
    if seam and len(loaded) == 2:
        (sa, sb) = loaded.values()
        first, second = (sa, sb) if sa.index.min() <= sb.index.min() else (sb, sa)
        lo, hi = sorted((first.index.max(), second.index.min()))
        pad = pd.Timedelta(days=2)
        fig.add_vrect(x0=lo - pad, x1=hi + pad, fillcolor="rgba(147,197,253,0.30)", line_width=0,
                      annotation_text="seam", annotation_position="top left")
    fig.update_layout(height=340, template="plotly_white", margin=dict(t=30, l=45, r=15, b=30),
                      legend=dict(orientation="h", y=1.12))
    return fig


_EDGE_STYLE = {"merge": dict(color="#16a34a", dash="solid", width=2.5),
               "distinct": dict(color="#dc2626", dash="dash", width=2),
               "undecided": dict(color="#9ca3af", dash="dot", width=1.2)}


def family_figure(fam: dict):
    """The conflict family in real space: nodes at the sensors' relative positions (meters), edges styled by verdict -- solid green merge, dashed red distinct, dotted gray undecided. Built from the plainest plotly primitives (one two-point trace per edge, a text trace for labels) because fancier constructions have failed to render in the panel's plotly.js."""
    import math

    import plotly.graph_objects as go

    # flat-earth local meters, NOT pyproj: at family scale (<= 2 km) the equirectangular projection is
    # exact to centimeters, and unlike a PROJ transform it cannot fail on environment (a mis-set
    # PROJ_LIB in the serving process once turned every coordinate into NaN and the graph into nothing)
    M_PER_DEG = 111_320.0
    located = {m: fam["facts"][m] for m in fam["members"]
               if fam["facts"][m]["lat"] is not None and not pd.isna(fam["facts"][m]["lat"])}
    missing = [m for m in fam["members"] if m not in located]
    xy = {}
    if located:
        lat0 = sum(float(f["lat"]) for f in located.values()) / len(located)
        lon0 = sum(float(f["lon"]) for f in located.values()) / len(located)
        klon = M_PER_DEG * math.cos(math.radians(lat0))
        xy = {m: ((float(f["lon"]) - lon0) * klon, (float(f["lat"]) - lat0) * M_PER_DEG)
              for m, f in located.items()}
    for i, m in enumerate(missing):
        xy[m] = (30.0 + 15 * i, -30.0)
    groups = {}
    for m in fam["members"]:
        groups.setdefault((round(xy[m][0], 1), round(xy[m][1], 1)), []).append(m)
    span = max((math.dist(a, b) for a in groups for b in groups if a != b), default=0.0)
    r = max(2.0, span * 0.12) if span else 6.0
    for pos, ms in groups.items():
        if len(ms) < 2:
            continue
        for i, m in enumerate(sorted(ms)):
            ang = 2 * math.pi * i / len(ms)
            xy[m] = (pos[0] + r * math.cos(ang), pos[1] + r * math.sin(ang))

    fig = go.Figure()
    seen = set()
    lx, ly, lt = [], [], []
    for e in fam["edges"]:
        st = _EDGE_STYLE[e["verdict"]]
        (x0, y0), (x1, y1) = xy[e["a"]], xy[e["b"]]
        fig.add_trace(go.Scatter(
            x=[round(x0, 2), round(x1, 2)], y=[round(y0, 2), round(y1, 2)],
            mode="lines", name=e["verdict"], legendgroup=e["verdict"],
            showlegend=e["verdict"] not in seen,
            line=dict(color=st["color"], dash=st["dash"], width=st["width"]),
            hoverinfo="skip"))
        seen.add(e["verdict"])
        sep = e.get("sep_m")
        if pd.notna(sep) and str(sep) != "":
            lx.append(round((x0 + x1) / 2, 2))
            ly.append(round((y0 + y1) / 2, 2))
            lt.append(f"{float(sep):.0f}m")
    if lx:
        fig.add_trace(go.Scatter(x=lx, y=ly, text=lt, mode="text",
                                 textfont=dict(size=9, color="#666"),
                                 showlegend=False, hoverinfo="skip"))
    hover = [f"{m}<br>{fam['facts'][m]['name']}<br>type: {fam['facts'][m]['type']}"
             f"<br>span: {fam['facts'][m]['span'][0]} .. {fam['facts'][m]['span'][1]}"
             for m in fam["members"]]
    fig.add_trace(go.Scatter(
        x=[round(xy[m][0], 2) for m in fam["members"]],
        y=[round(xy[m][1], 2) for m in fam["members"]],
        mode="markers+text", text=[m.split(":")[-1][-4:] for m in fam["members"]],
        textposition="top center", textfont=dict(size=10),
        marker=dict(size=13, color="#1f2937"), hovertext=hover, hoverinfo="text",
        name="sensors"))
    xs_all = [xy[m][0] for m in fam["members"]]
    ys_all = [xy[m][1] for m in fam["members"]]
    ccx, ccy = (max(xs_all) + min(xs_all)) / 2, (max(ys_all) + min(ys_all)) / 2
    half = max(max(xs_all) - min(xs_all), max(ys_all) - min(ys_all), 10.0) * 0.62
    fig.update_layout(height=360, template="plotly_white",
                      margin=dict(t=26, l=30, r=10, b=30),
                      legend=dict(orientation="h", y=1.15),
                      xaxis=dict(title="meters (E-W)", range=[ccx - half, ccx + half]),
                      yaxis=dict(title="meters (N-S)", range=[ccy - half, ccy + half]))
    return fig


def masked_figure(raw: pd.DataFrame, masked: pd.DataFrame, channel: str):
    """Masked beside raw: what the uncommitted span/ceiling values would actually remove."""
    import plotly.graph_objects as go

    col = f"{channel}_mean"
    fig = go.Figure()
    for frame, name, color, width in ((raw, "published today", "#bbbbbb", 2.5),
                                      (masked, "with your masks", A_COLOR, 1.0)):
        if frame is None or col not in getattr(frame, "columns", []):
            continue
        d = frame.dropna(subset=[col])
        fig.add_trace(go.Scatter(x=pd.to_datetime(d.date), y=d[col], mode="lines",
                                 line=dict(width=width, color=color), name=name,
                                 hovertemplate="%{x|%Y-%m-%d}<br>%{y:.3f}<extra></extra>"))
    fig.update_layout(height=300, template="plotly_white", margin=dict(t=30, l=45, r=15, b=30),
                      legend=dict(orientation="h", y=1.15))
    return fig

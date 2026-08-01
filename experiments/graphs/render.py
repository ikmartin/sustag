"""One PNG per interval from intervals.py, then a GIF and a seekable MP4 of the whole sequence.

THE EDGE SET IS THE TRANSITIVE REDUCTION OF THE ACTIVE SUBGRAPH, and the order of those two operations is the whole point. `basin_containment_graph.parquet` is transitively closed -- if A drains into B and B into C it also carries A->C -- so drawing it raw gives a hairball in which nesting depth is invisible. Reducing the FULL graph and then filtering to the active sites would be wrong in the other direction: it would drop the A->C edge because B lies between them, even in an interval when B is not deployed and A really does flow to C with nothing measured in between. So the subgraph is induced first and reduced second, and "no basin lies between them" means no ACTIVE basin.

Styling mirrors the widget (`widget/colors.py`): IWQIS sites limegreen on darkgreen, USGS #55a3f7 on #6b21a8, rivers #2563eb. Flowlines come from `widget/assets/iowa_flowlines.geojson` (NHD stream order >= 5, pre-simplified) so the figure shows the same river network the widget does. The Iowa outline is read from the local `notes/graphics/_us_states.geojson` rather than the Census TIGER URL `map_common.load_iowa_geojson` uses -- no network call, so this runs offline.

Every frame shares one extent (the cohort bounding box unioned with Iowa's, padded), so nothing moves between frames and the growth of the network is the only thing changing.

Usage:
    python render.py                 # all 159 frames + the gif
    python render.py --limit 6       # smoke test
    python render.py --video-only    # rebuild the gif + mp4 from existing PNGs
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import networkx as nx
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

import intervals as iv_mod  # noqa: E402
from src.data.access import get_basin_graph, get_metadata, get_site_ids  # noqa: E402

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
FRAMES = IMAGES / "site_graphs"  # 159 per-interval PNGs; nested so images/ stays readable
GIF = IMAGES / "graph_evolution.gif"
MP4 = IMAGES / "graph_evolution.mp4"

_IOWA_GEOJSON = _ROOT / "notes" / "graphics" / "_us_states.geojson"
_FLOWLINES = _ROOT / "widget" / "assets" / "iowa_flowlines.geojson"

# widget/colors.py -- SITE_DEFAULT (IWQIS), SITE_USGS, HYDRO
C_IWQIS = {"fill": "limegreen", "stroke": "darkgreen"}
C_USGS = {"fill": "#55a3f7", "stroke": "#6b21a8"}
C_RIVER = "#2563eb"

PAD = 0.02  # fraction of span added on every side
DPI = 200
FIG_W = 12.0
TITLE_IN, FOOT_IN = 0.62, 0.40  # inches reserved above/below the map, so the axes itself fills the width
GIF_WIDTH = 1100  # downscale for the gif; the PNGs stay full resolution
GIF_MS = 500  # 0.5 s per frame
MP4_WIDTH = 1600
MP4_OUT_FPS = 30  # each frame still holds 0.5 s (input rate is 1000/GIF_MS); a normal output rate is what makes scrubbing smooth


def _locations() -> dict[str, tuple[float, float]]:
    """site_uid -> (lon, lat) for the whole cohort."""
    md = get_metadata()
    cohort = {str(s) for s in get_site_ids()}
    return {
        str(r.site_uid): (float(r.longitude), float(r.latitude))
        for r in md.itertuples(index=False)
        if str(r.site_uid) in cohort
    }


def _extent(loc: dict[str, tuple[float, float]], iowa: gpd.GeoDataFrame) -> tuple[float, float, float, float]:
    """Fixed (xmin, xmax, ymin, ymax) holding every cohort site AND the whole state outline, padded."""
    xs = [p[0] for p in loc.values()]
    ys = [p[1] for p in loc.values()]
    bx0, by0, bx1, by1 = iowa.total_bounds
    x0, x1 = min(min(xs), bx0), max(max(xs), bx1)
    y0, y1 = min(min(ys), by0), max(max(ys), by1)
    dx, dy = (x1 - x0) * PAD, (y1 - y0) * PAD
    return x0 - dx, x1 + dx, y0 - dy, y1 + dy


def _reduce(g: nx.DiGraph, active: list[str]) -> nx.DiGraph:
    """Transitive reduction of the subgraph induced on `active` -- an edge survives only if no OTHER ACTIVE basin lies between its endpoints.

    The full graph is not a DAG (3 two-cycles, from site pairs that are one gauge re-registered). Those pairs have zero overlapping record, so no interval should contain both; if one ever does, the cycle is broken by keeping the edge into the larger basin rather than failing the render.
    """
    sub = g.subgraph([n for n in active if n in g]).copy()
    if not nx.is_directed_acyclic_graph(sub):
        for a, b in list(sub.edges()):
            if sub.has_edge(b, a):
                area_ab = sub[a][b].get("parent_area", 0.0)
                area_ba = sub[b][a].get("parent_area", 0.0)
                sub.remove_edge(*((b, a) if area_ab >= area_ba else (a, b)))
    return nx.transitive_reduction(sub)


def _draw(entry: dict, g: nx.DiGraph, loc, iowa, rivers, extent, out: Path) -> None:
    x0, x1, y0, y1 = extent
    mid_lat = (y0 + y1) / 2
    aspect = 1.0 / np.cos(np.deg2rad(mid_lat))
    # The axes is placed by hand rather than via subplots: matplotlib shrinks an aspect-constrained
    # axes inside its box, which left the map filling barely half the frame.
    map_h = FIG_W * ((y1 - y0) * aspect) / (x1 - x0)
    fig_h = map_h + TITLE_IN + FOOT_IN

    fig = plt.figure(figsize=(FIG_W, fig_h), dpi=DPI)
    ax = fig.add_axes([0.0, FOOT_IN / fig_h, 1.0, map_h / fig_h])
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    rivers.plot(ax=ax, color=C_RIVER, linewidth=0.5, alpha=0.5, zorder=1)
    iowa.boundary.plot(ax=ax, color="#333333", linewidth=1.4, alpha=0.5, zorder=2)

    active = [s for s in entry["active_sites"] if s in loc]
    red = _reduce(g, active)

    segs = [(loc[c], loc[p]) for c, p in red.edges() if c in loc and p in loc]
    if segs:
        ax.add_collection(LineCollection(segs, colors="#111111", linewidths=1.1, alpha=0.75, zorder=3))
        for (cx, cy), (px, py) in segs:  # arrowhead only, child -> parent (downstream)
            ax.annotate(
                "",
                xy=(px, py),
                xytext=(cx, cy),
                arrowprops=dict(arrowstyle="-|>", color="#111111", alpha=0.75, lw=0, shrinkA=0, shrinkB=4),
                zorder=3,
            )

    for group, c in ((["USGS"], C_USGS), ([], C_IWQIS)):
        pts = [loc[s] for s in active if (s.startswith("USGS") if group else not s.startswith("USGS"))]
        if pts:
            ax.scatter(
                [p[0] for p in pts], [p[1] for p in pts],
                s=70, c=c["fill"], edgecolors=c["stroke"], linewidths=1.2, zorder=4,
            )

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect(aspect)
    ax.set_axis_off()
    fig.text(0.5, 1 - 0.42 * TITLE_IN / fig_h, f"{entry['start_date']} through {entry['end_date']}",
             ha="center", va="center", fontsize=19)
    n_usgs = sum(1 for s in active if s.startswith("USGS"))
    fig.text(0.5, 0.44 * FOOT_IN / fig_h,
             f"{len(active)} active sites ({n_usgs} USGS, {len(active) - n_usgs} IWQIS) · {red.number_of_edges()} edges",
             ha="center", va="center", fontsize=12, color="#444444")
    ax.legend(
        handles=[
            Line2D([], [], marker="o", ls="", mfc=C_IWQIS["fill"], mec=C_IWQIS["stroke"], ms=9, label="IWQIS"),
            Line2D([], [], marker="o", ls="", mfc=C_USGS["fill"], mec=C_USGS["stroke"], ms=9, label="USGS"),
        ],
        loc="upper left", frameon=False, fontsize=12,
    )

    fig.savefig(out, dpi=DPI, facecolor="white", bbox_inches=None)
    plt.close(fig)


def build_gif(paths: list[Path]) -> None:
    """PNGs -> animated GIF at GIF_MS per frame, downscaled to GIF_WIDTH, every frame sharing ONE palette.

    Quantising each frame independently silently wrecks the colours. The 2008-2010 frames contain no IWQIS site at all, so limegreen exists in them only as the ~350-pixel legend swatch -- median cut drops something that small, PIL then applies frame 0's palette to the whole animation, and IWQIS renders (92,107,109) grey for all 159 frames while the PNGs are correct. So the palette is derived ONCE from a source image that is guaranteed to contain every colour that matters: the densest frame with an explicit swatch bar of the key colours painted over it.
    """
    from PIL import Image, ImageDraw

    ims = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        h = round(im.height * GIF_WIDTH / im.width)
        ims.append(im.resize((GIF_WIDTH, h), Image.LANCZOS))

    keys = ["white", "#111111", "#333333", "#444444", C_RIVER,
            C_IWQIS["fill"], C_IWQIS["stroke"], C_USGS["fill"], C_USGS["stroke"]]
    src = max(ims, key=lambda im: int((np.asarray(im) < 250).any(axis=-1).sum())).copy()
    d = ImageDraw.Draw(src)
    for i, c in enumerate(keys):  # big blocks, so no key colour can be outvoted by the white background
        d.rectangle([i * 60, 0, i * 60 + 59, 59], fill=tuple(round(255 * v) for v in to_rgb(c)))
    pal = src.quantize(colors=256, method=Image.MAXCOVERAGE)

    frames = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in ims]
    frames[0].save(GIF, save_all=True, append_images=frames[1:], duration=GIF_MS, loop=0, optimize=True)
    print(f"  {GIF.name}  {len(frames)} frames @ {GIF_MS} ms  ({GIF.stat().st_size / 1e6:.1f} MB)")


def build_mp4(paths: list[Path]) -> None:
    """PNGs -> H.264 MP4, same 0.5 s per frame as the gif but seekable, so it can be paused and scrubbed.

    Input rate is 1000/GIF_MS so a frame still holds half a second; output is re-timed to MP4_OUT_FPS because a 2 fps H.264 stream seeks in half-second lurches in most players. yuv420p and an even-numbered scale are what make it play outside ffmpeg.
    """
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        print("  ffmpeg not on PATH -- skipping mp4 (the gif is unaffected)")
        return
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", f"{1000 / GIF_MS:g}",
        "-pattern_type", "glob", "-i", str(paths[0].parent / "*.png"),
        "-vf", f"scale={MP4_WIDTH}:-2,format=yuv420p",
        "-r", str(MP4_OUT_FPS), "-c:v", "libx264", "-crf", "20", "-preset", "slow",
        "-movflags", "+faststart", str(MP4),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"  ffmpeg failed ({r.returncode}): {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else '?'}")
        return
    print(f"  {MP4.name}  {len(paths)} frames @ {GIF_MS} ms  ({MP4.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="render only the first N intervals (smoke test)")
    ap.add_argument("--video-only", "--gif-only", dest="video_only", action="store_true",
                    help="skip rendering, rebuild the gif and mp4 from images/site_graphs/")
    a = ap.parse_args()

    FRAMES.mkdir(parents=True, exist_ok=True)
    entries = iv_mod.load()
    if a.limit:
        entries = entries[: a.limit]

    if a.video_only:
        existing = sorted(FRAMES.glob("*.png"))
        build_gif(existing)
        build_mp4(existing)
        return

    g = get_basin_graph()
    loc = _locations()
    iowa = gpd.read_file(_IOWA_GEOJSON)
    iowa = iowa[iowa["name"].astype(str).str.lower() == "iowa"]
    rivers = gpd.read_file(_FLOWLINES)
    extent = _extent(loc, iowa)
    print(f"extent {[round(v, 3) for v in extent]}  ({len(loc)} cohort sites, {len(rivers)} flowlines)")

    paths = []
    for i, e in enumerate(entries):
        out = FRAMES / f"{i:03d}_{e['start_date']}.png"
        _draw(e, g, loc, iowa, rivers, extent, out)
        paths.append(out)
        if (i + 1) % 20 == 0 or i + 1 == len(entries):
            print(f"  {i + 1}/{len(entries)} frames")

    mb = [p.stat().st_size / 1e6 for p in paths]
    print(f"{len(paths)} PNGs in {FRAMES}/  (largest {max(mb):.2f} MB, total {sum(mb):.0f} MB)")
    build_gif(paths)
    build_mp4(paths)


if __name__ == "__main__":
    main()

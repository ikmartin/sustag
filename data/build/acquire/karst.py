"""The Weary & Doctor karst map (USGS OFR 2014-1156) -- carbonate and evaporite units classified by BURIAL DEPTH.

WHY BURIAL DEPTH IS THE POINT. StreamCat's `pctcarbresid` reports carbonate *residual* material, so a carbonate aquifer under forty feet of glacial till scores zero -- while being exactly the cover-collapse setting that routes nitrate to groundwater with almost no attenuation. Our AOE straddles the glacial margin, which is where that distinction lives and where a residual-material measure is most misleading.

WHAT WEAKENED SINCE THIS WAS PROPOSED, and it belongs here rather than in a commit message: the original justification was "no substitute on disk", established by searching 14,139 Wieczorek and 167 StreamCat attribute names for the word *karst*. Since then the Wieczorek **Hydrologic** theme has been acquired, and it carries the flow-pathway partition -- `CAT_CONTACT`, `CAT_BFI`, `CAT_IEOF`, `CAT_SATOF`, `CAT_RECHG` -- which is functionally what karst PREDICTS. Karst may still add, because burial depth is a fact no flow-pathway attribute encodes, but that is now a question for the incremental-information measure rather than an assumption. Acquired so it can be answered.

THE DOWNLOAD 403s WITHOUT A BROWSER USER-AGENT. `pubs.usgs.gov` rejects the default urllib agent, and the failure presents as an HTTP error that reads like a missing file rather than a header problem.
"""

from __future__ import annotations

from .. import config

URL = "https://pubs.usgs.gov/of/2014/1156/downloads/USKarstMap.zip"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def _raw_dir():
    return config.RAW / "karst"


def download(force: bool = False):
    """Fetch the release zip into `raw/karst/`. Returns the path; a no-op when already present."""
    import urllib.request

    d = _raw_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "USKarstMap.zip"
    if p.exists() and not force:
        return p
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    tmp = p.with_suffix(".zip.tmp")
    with urllib.request.urlopen(req, timeout=1800) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(p)
    return p


def layers() -> list:
    """The vector layers inside the zip, without extracting it."""
    import pyogrio

    p = _raw_dir() / "USKarstMap.zip"
    if not p.exists():
        return []
    return [str(n) for n in pyogrio.list_layers(f"zip://{p}")[:, 0]]

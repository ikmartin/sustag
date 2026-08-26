import numpy as np
import pandas as pd
import pytest

from data.build.derive import geometry


def test_half_widths_are_rectangles_not_circles():
    # 2 dp lat, 4 dp lon at 43N: tall thin sliver
    hla, hlo = geometry.half_widths_m([43.0], [2], [4])
    assert abs(hla[0] - 556.6) < 1.0
    assert abs(hlo[0] - 4.07) < 0.1
    # null dp -> zero slack, never invented slack
    hla, hlo = geometry.half_widths_m([43.0], [None], [None])
    assert hla[0] == 0.0 and hlo[0] == 0.0


def test_rect_bounds_interval_arithmetic():
    # two points 100 m apart on x, each +-10 m in x, exact in y
    mn, mx = geometry._rect_bounds(np.array([100.0]), np.array([0.0]),
                                   np.array([10.0]), np.array([0.0]),
                                   np.array([10.0]), np.array([0.0]))
    assert mn[0] == 80.0 and mx[0] == 120.0
    # overlapping rectangles -> min 0
    mn, mx = geometry._rect_bounds(np.array([5.0]), np.array([0.0]),
                                   np.array([10.0]), np.array([0.0]),
                                   np.array([10.0]), np.array([0.0]))
    assert mn[0] == 0.0


def _sensors(rows):
    cols = dict(site_uid=[], lat=[], lon=[], coord_dp_lat=[], coord_dp_lon=[], comid=[])
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k))
    return pd.DataFrame(cols)


def test_proximity_table_from_stated_coords(monkeypatch):
    import data.build.config as config

    monkeypatch.setattr(config, "PROXIMITY_RADIUS_M", 2000.0)
    s = _sensors([
        dict(site_uid="USGS:A", lat=42.0, lon=-93.0, coord_dp_lat=6, coord_dp_lon=6, comid=1),
        dict(site_uid="IWQIS:B", lat=42.0005, lon=-93.0, coord_dp_lat=2, coord_dp_lon=2, comid=1),
        dict(site_uid="USGS:C", lat=42.5, lon=-93.5, coord_dp_lat=6, coord_dp_lon=6, comid=2),  # far away
        dict(site_uid="IWQIS:NOLOC", lat=None, lon=None, coord_dp_lat=None, coord_dp_lon=None, comid=None),
    ])
    p = geometry.proximity_table(s)
    assert len(p) == 1                                   # C is beyond 2 km; NOLOC pairs with nothing
    r = p.iloc[0]
    assert (r.uid_a, r.uid_b) == ("IWQIS:B", "USGS:A")   # canonical lexical order
    assert 40 < r.sep_m < 70                             # ~55.7 m
    # B's 2-dp rectangle (~556x412 m half-widths) swallows the separation: min hits 0
    assert r.min_sep_m == 0.0
    assert r.max_sep_m > r.sep_m
    assert bool(r.same_comid)


def test_proximity_same_comid_null_safe(monkeypatch):
    import data.build.config as config

    monkeypatch.setattr(config, "PROXIMITY_RADIUS_M", 2000.0)
    s = _sensors([
        dict(site_uid="USGS:A", lat=42.0, lon=-93.0, coord_dp_lat=6, coord_dp_lon=6, comid=None),
        dict(site_uid="USGS:B", lat=42.0005, lon=-93.0, coord_dp_lat=6, coord_dp_lon=6, comid=None),
    ])
    p = geometry.proximity_table(s)
    assert not bool(p.iloc[0].same_comid)                # two nulls are not the same reach

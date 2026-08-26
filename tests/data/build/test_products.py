"""D5 kernels: exact zonal fractions, the weather cell-id convention (the 1,351,290 regression), closures."""

import numpy as np
import pandas as pd
import pytest

from data.build.acquire import weather
from data.build.derive import products, zonal


class FakeCat:
    """Minimal stand-in for zonal.Catchments: hands out one batch of prepared geometry."""

    def __init__(self, comids, geoms, areas):
        import geopandas as gpd

        self.comid = np.asarray(comids, dtype="int64")
        self.area_km2 = np.asarray(areas, dtype="float64")
        self._g = gpd.GeoDataFrame({"comid": self.comid}, geometry=geoms, crs=4326)
        self.n_repaired = 0

    def __len__(self):
        return len(self.comid)

    def release(self):
        pass

    def batches(self, crs, size=zonal.BATCH, limit=None, skip=None):
        g = self._g if str(crs).upper() in ("EPSG:4326", "4326") else self._g.to_crs(crs)
        yield self.comid, g


def _raster(path, arr, west=0.0, north=10.0, res=1.0, crs="EPSG:4326"):
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype=str(arr.dtype), crs=crs,
                       transform=from_origin(west, north, res, res)) as d:
        d.write(arr, 1)
    return path


class TestZonalEngine:
    def test_exact_fractions_on_the_golden_grid(self, tmp_path):
        """Three boxes over a 10x10 unit raster: pixel-aligned, half-pixel offset, and one covering a boundary strip. `count` must be the EXACT area in pixel units -- the whole point of exactextract over rasterization."""
        from shapely.geometry import box

        arr = np.arange(100, dtype="int32").reshape(10, 10) % 3
        r = _raster(tmp_path / "g.tif", arr)
        cat = FakeCat([1, 2, 3],
                      [box(0, 6, 4, 10),          # 16 whole pixels
                       box(0.5, 0.5, 2.5, 2.5),   # 4 pixel-areas, all partial
                       box(0, 5.5, 10, 6.5)],     # a 10 x 1 strip across a pixel row boundary
                      [1, 1, 1])
        out = zonal.extract(cat, {"g": r}, "EPSG:4326", ops=("count",))
        got = dict(zip(out.comid, out.g_count))
        assert got[1] == pytest.approx(16.0)
        assert got[2] == pytest.approx(4.0)
        assert got[3] == pytest.approx(10.0)

    def test_categorical_fractions_close(self, tmp_path):
        """frac * count folded over every value present must reproduce count exactly -- the closure the crops reduce depends on."""
        from shapely.geometry import box

        arr = np.tile(np.array([[1, 2]], dtype="int32"), (10, 5))
        r = _raster(tmp_path / "c.tif", arr)
        cat = FakeCat([7], [box(0.25, 0.25, 9.75, 9.75)], [1])
        out = zonal.extract(cat, {"c": r}, "EPSG:4326", ops=zonal.CATEGORICAL_OPS)
        row = out.iloc[0]
        total = float(np.asarray(row.c_frac, dtype="float64").sum())
        assert total == pytest.approx(1.0)
        per_value = np.asarray(row.c_frac, dtype="float64") * float(row.c_count)
        assert per_value.sum() == pytest.approx(float(row.c_count))

    def test_mixed_grid_group_raises(self, tmp_path):
        from shapely.geometry import box

        a = _raster(tmp_path / "a.tif", np.zeros((10, 10), dtype="int32"))
        b = _raster(tmp_path / "b.tif", np.zeros((5, 5), dtype="int32"), res=2.0)
        cat = FakeCat([1], [box(0, 0, 5, 5)], [1])
        with pytest.raises(ValueError, match="share a grid"):
            zonal.extract(cat, {"a": a, "b": b}, "EPSG:4326", ops=("count",))


class TestCellIdConvention:
    def test_area_decode_uses_the_weather_origin(self):
        """The 1,351,290 regression: decode the row from THE WEATHER STORE'S origin (49.4N), not 90N. A cell at ~41.8N (Iowa) must get the area of 41.8N, which the 90N decode would place at a very different latitude for the same id."""
        cid = weather.cell_id(-93.5, 41.775)
        area = float(products.cell_area_km2(cid)[()])
        lat = np.radians(41.775)
        expected = (products._EARTH_R_KM ** 2 * np.radians(weather._RES)
                    * (np.sin(lat + np.radians(weather._RES / 2)) - np.sin(lat - np.radians(weather._RES / 2))))
        assert area == pytest.approx(expected, rel=1e-6)

    def test_negative_ids_north_of_the_origin_are_legal(self):
        """The AOE reaches 49.7N; gridMET's origin row is 49.4N, so northern cells carry NEGATIVE ids. They must decode to the right latitude and never collide with the raster sentinel."""
        cid = int(weather.cell_id(-96.0, 49.7)[()])
        assert cid < 0 and cid != products._NODATA
        row = int(np.floor_divide(cid, weather._COLS))
        assert weather._LAT0 - row * weather._RES == pytest.approx(49.7, abs=weather._RES)
        assert float(products.cell_area_km2(cid)[()]) == pytest.approx(13.9, abs=0.3)

    def test_reduce_keeps_negative_ids_drops_only_sentinel(self):
        res = pd.DataFrame({
            "gridmet_count": [2.0],
            "gridmet_unique": [np.array([-10718, products._NODATA, 55000], dtype="int64")],
            "gridmet_frac": [np.array([0.5, 0.25, 0.25])],
        })
        out = products._weights_reduce(np.array([42]), res)
        assert set(out.cell_id) == {-10718, 55000}, "negative ids are real cells; only the sentinel drops"
        a = out.set_index("cell_id").w_km2
        assert a[-10718] == pytest.approx(0.5 * 2.0 * products.cell_area_km2(-10718), rel=1e-6)

    def test_id_raster_is_the_weather_function_not_a_copy(self):
        """id_raster must call weather.cell_id -- the ancestor's reimplementation is the bug's origin."""
        import inspect

        src = inspect.getsource(products.id_raster)
        assert "weather.cell_id(" in src


class TestClosures:
    def test_crops_closure_passes_and_fails(self):
        # LONG format: raw codes, fractions of covered area summing to 1 per (comid, year)
        good = pd.DataFrame({"comid": [1, 1, 1], "year": [2020] * 3,
                             "code": [1, 5, 36], "frac": [0.6, 0.3, 0.1], "n_px": [100.0] * 3})
        ok, _ = products._crops_closure(good, None)
        assert ok
        bad = good.assign(frac=[0.6, 0.3, 0.3])
        ok, note = products._crops_closure(bad, None)
        assert not ok and "do not sum to 1" in note

    def test_crops_reduce_emits_raw_codes_long(self):
        res = pd.DataFrame({"2020_unique": [[1, 5], [36]], "2020_frac": [[0.7, 0.3], [1.0]],
                            "2020_count": [50.0, 8.0]})
        out = products._crops_reduce(np.array([11, 22]), res, [2020])
        assert list(out.columns) == ["comid", "year", "code", "frac", "n_px"]
        assert out.code.tolist() == [1, 5, 36]           # raw codes, never class names
        assert out[out.comid == 11].frac.sum() == pytest.approx(1.0)
        assert (out[out.comid == 22].n_px == 8.0).all()

    def test_nutrients_closure(self):
        df = pd.DataFrame({"kg_per_ha": [2.0], "n_px": [10.0],
                           "kg": [2.0 * 10.0 * products.HA_PER_PIXEL]})
        assert products._nutrients_closure(df, None)[0]
        assert not products._nutrients_closure(df.assign(kg=1.0), None)[0]

    def test_agtile_closure(self):
        df = pd.DataFrame({"tile_px": [5.0], "n_px": [10.0]})
        assert products._agtile_closure(df, None)[0]
        assert not products._agtile_closure(df.assign(tile_px=11.0), None)[0]

    def test_weights_closure_catches_zero_join(self, monkeypatch):
        """Zero joining cells is exactly how the offset bug presented -- the closure must call it broken."""
        grid = pd.DataFrame({"cell_id": [100, 101, 102]})
        monkeypatch.setattr(products, "_grid", lambda: grid)
        w = pd.DataFrame({"comid": [1], "cell_id": [999999], "w_km2": [1.0]})
        cov = pd.DataFrame({"comid": [1], "area_km2": [1.0]})
        ok, note = products._weights_closure(w, cov)
        assert not ok

    def test_weights_closure_area_conservation(self, monkeypatch):
        grid = pd.DataFrame({"cell_id": [100, 101]})
        monkeypatch.setattr(products, "_grid", lambda: grid)
        w = pd.DataFrame({"comid": [1, 1], "cell_id": [100, 101], "w_km2": [0.6, 0.4]})
        cov = pd.DataFrame({"comid": [1], "area_km2": [1.0]})
        ok, note = products._weights_closure(w, cov)
        assert ok, note

    def test_weights_global_conservation_no_cell_claimed_twice(self, monkeypatch):
        """Summed over every catchment touching it, a cell's claimed area may not exceed its ground area."""
        grid = pd.DataFrame({"cell_id": [100]})
        monkeypatch.setattr(products, "_grid", lambda: grid)
        area = float(products.cell_area_km2(np.array([100]))[0])
        cov = pd.DataFrame({"comid": [1, 2], "area_km2": [area, area]})
        # two catchments together claim 2x the cell -- a double claim the per-catchment check cannot see
        w = pd.DataFrame({"comid": [1, 2], "cell_id": [100, 100], "w_km2": [area, area]})
        ok, note = products._weights_closure(w, cov)
        assert not ok and "claimed beyond" in note
        ok, _ = products._weights_closure(w.assign(w_km2=[0.3, 0.2]), cov)
        assert not ok

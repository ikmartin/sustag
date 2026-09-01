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

    def test_nutrients_component_closure_tolerates_rounding_but_not_a_missing_part(self):
        """The per-pixel allowance must absorb storage rounding and still catch an absent component."""
        def frame(n_px, ag_kg, crop_kg, past_kg):
            rows = []
            for prod, kg in (("fixation_ag", ag_kg), ("fixation_cropland", crop_kg),
                             ("fixation_pasture", past_kg)):
                rows.append({"comid": 1, "year": 2010, "product": prod, "n_px": float(n_px),
                             "kg": kg, "kg_per_ha": kg / (n_px * products.HA_PER_PIXEL)})
            return pd.DataFrame(rows)

        # rounding-scale disagreement, right at the derived per-pixel ceiling
        n_px = 100
        slack = products.GTREND_PX_TOL * n_px
        ok, note = products._nutrients_closure(frame(n_px, 600.0 + slack * 0.9, 400.0, 200.0), None)
        assert ok, note

        # a component genuinely missing from the total -- must still fail
        ok, note = products._nutrients_closure(frame(n_px, 400.0, 400.0, 200.0), None)
        assert not ok and "fixation_ag" in note

        # and the allowance must scale with pixel count, not sit at a fixed value
        big = products.GTREND_PX_TOL * 10_000
        assert products._nutrients_closure(frame(10_000, 600.0 + big * 0.9, 400.0, 200.0), None)[0]
        assert not products._nutrients_closure(frame(10, 600.0 + big * 0.9, 400.0, 200.0), None)[0]

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



class TestRasterGroups:
    """`zonal._raster_groups` sizes a request to GDAL's block cache -- see the nutrients nine-day pass."""

    def _tif(self, tmp_path, name, block, dtype="float32", n=1):
        import rasterio
        from rasterio.transform import from_origin

        paths = []
        for i in range(n):
            p = tmp_path / f"{name}{i}.tif"
            with rasterio.open(p, "w", driver="GTiff", width=block * 2, height=block * 2, count=1,
                               dtype=dtype, crs="EPSG:5070", transform=from_origin(0, 0, 250, 250),
                               tiled=True, blockxsize=block, blockysize=block) as dst:
                dst.write(np.zeros((block * 2, block * 2), dtype=dtype), 1)
            paths.append(p)
        return paths

    def test_small_tiles_stay_one_group(self, tmp_path):
        paths = self._tif(tmp_path, "small", 256, n=8)
        assert zonal._raster_groups(paths) == [paths]

    def test_many_large_tiled_rasters_are_split(self, tmp_path, monkeypatch):
        import rasterio.env

        paths = self._tif(tmp_path, "big", 1024, n=6)
        # A deliberately small cache: 6 rasters x 4 blocks x 4 MB cannot fit in 32 MB. Only GDAL_CACHEMAX is
        # answered -- rasterio calls this for its own keys with `normalize=`, and a blanket lambda breaks
        # `rasterio.open`, which the probe would then swallow into a single group.
        real = rasterio.env.get_gdal_config
        monkeypatch.setattr(rasterio.env, "get_gdal_config",
                            lambda k, **kw: (32 << 20) if k == "GDAL_CACHEMAX" else real(k, **kw))
        groups = zonal._raster_groups(paths)
        assert len(groups) > 1
        assert sum(len(g) for g in groups) == len(paths)
        assert [p for g in groups for p in g] == paths

    def test_unprobeable_input_keeps_one_group(self, tmp_path):
        missing = [tmp_path / "nope.tif"]
        assert zonal._raster_groups(missing) == [missing]


class TestCheckpointInputKey:
    """`zonal._adopt_or_clear` keys a checkpoint directory on the inputs that produced it."""

    def _dir(self, tmp_path):
        d = tmp_path / "parts"
        d.mkdir()
        (d / "part_00000.parquet").write_bytes(b"x")
        (d / "part_00001.parquet").write_bytes(b"y")
        return d

    def test_missing_stamp_is_adopted_not_discarded(self, tmp_path):
        """Parts predating the guard are a long pass's output and there is no evidence against them."""
        d = self._dir(tmp_path)
        zonal._adopt_or_clear(d, "A")
        assert len(list(d.glob("part_*.parquet"))) == 2
        assert (d / ".inputs").read_text().strip() == "A"

    def test_same_inputs_keep_the_parts(self, tmp_path):
        d = self._dir(tmp_path)
        zonal._adopt_or_clear(d, "A")
        zonal._adopt_or_clear(d, "A")
        assert len(list(d.glob("part_*.parquet"))) == 2

    def test_changed_inputs_discard_the_parts(self, tmp_path):
        d = self._dir(tmp_path)
        zonal._adopt_or_clear(d, "A")
        zonal._adopt_or_clear(d, "B")
        assert list(d.glob("part_*.parquet")) == []
        assert (d / ".inputs").read_text().strip() == "B"

    def test_no_declared_inputs_is_a_noop(self, tmp_path):
        d = self._dir(tmp_path)
        zonal._adopt_or_clear(d, None)
        assert len(list(d.glob("part_*.parquet"))) == 2
        assert not (d / ".inputs").exists()

    def test_input_stamp_notices_a_rewritten_file(self, tmp_path):
        """A raster rewritten in place changes no value in the output -- only the stamp can see it."""
        from data.build import io as bio

        p = tmp_path / "r.tif"
        p.write_bytes(b"one")
        first = bio.input_stamp(p)
        p.write_bytes(b"two different length")
        assert bio.input_stamp(p) != first
        assert bio.input_stamp(tmp_path / "absent.tif") != bio.input_stamp(p)


class TestHuc8SurplusCheck:
    """The only external test of whether d5's extraction is RIGHT, so it must fail when it is wrong.

    Every other d5 check is internal and would pass on a reduction that is wrong the same way twice. `products.OUT` is patched rather than `config.COVARIATES_DIR` because the module binds it at import.
    """

    def _fixture(self, tmp_path, monkeypatch, our_scale=1.0):
        """A tiny world where our HUC8 aggregate equals theirs, scaled by `our_scale`.

        Six basins x 18 years clears the check's own 50-row floor, and the value VARIES by basin and year because a correlation over constants is NaN — a fixture with no variance cannot exercise a check whose headline is a correlation.
        """
        from data.build.acquire import network

        hucs = [f"0701010{i}" for i in range(1, 7)]
        per_reach, ha = 20, 100.0
        comids, huc = [], []
        for i, h in enumerate(hucs):
            comids += list(range(i * per_reach + 1, (i + 1) * per_reach + 1))
            huc += [h] * per_reach
        vaa_p = tmp_path / "vaa.parquet"
        pd.DataFrame({"comid": comids, "reachcode": [h + "000001" for h in huc]}).to_parquet(vaa_p, index=False)
        monkeypatch.setattr(network, "_path",
                            lambda layer: vaa_p if layer == "vaa" else tmp_path / f"{layer}.parquet")

        years = list(range(2000, 2018))

        def rate(h, y):                                  # varies on both axes, so corr is defined
            return 10.0 + 3.0 * hucs.index(h) + 0.5 * (y - 2000)

        rows = [{"comid": c, "year": y, "product": "surplus",
                 "kg": rate(h, y) * ha * our_scale, "n_px": ha / products.HA_PER_PIXEL}
                for c, h in zip(comids, huc) for y in years]
        monkeypatch.setattr(products, "OUT", tmp_path)
        pd.DataFrame(rows).to_parquet(tmp_path / "nutrients.parquet", index=False)

        ref = pd.DataFrame([{"huc8": h, "year": y, "AREASQKM": per_reach * ha / 100.0,
                             "kg_per_ha": rate(h, y)} for h in hucs for y in years])
        monkeypatch.setattr(products, "huc8_surplus_reference", lambda years=None: ref)

    def test_passes_when_our_reduction_agrees(self, tmp_path, monkeypatch):
        self._fixture(tmp_path, monkeypatch, our_scale=1.0)
        ok, detail = products._check_huc8_surplus()
        assert ok, detail
        assert "pearson" in detail

    def test_fails_when_our_reduction_is_biased(self, tmp_path, monkeypatch):
        """A 10% scale error is exactly the class of defect no internal closure can see."""
        self._fixture(tmp_path, monkeypatch, our_scale=1.10)
        ok, detail = products._check_huc8_surplus()
        assert ok is False, detail

    def test_skips_rather_than_fails_without_the_reference(self, tmp_path, monkeypatch):
        self._fixture(tmp_path, monkeypatch)
        monkeypatch.setattr(products, "huc8_surplus_reference", lambda years=None: None)
        ok, detail = products._check_huc8_surplus()
        assert ok is None


class TestDroughtYearCoverage:
    """`drought.fetch_year` must ask whether a year is COVERED, not whether its file exists.

    The current year is written partial — a file created in August holds pentads to August — so an existence guard freezes the newest year at whatever the first run fetched, and raises nothing.
    """

    def _write(self, tmp_path, year, last_date):
        import pyarrow as pa
        import pyarrow.parquet as pq

        p = tmp_path / f"gridmet_pentad_{year}.parquet"
        t = pa.table({"cell_id": [1, 2], "date": pd.to_datetime([f"{year}-01-05", last_date]).date})
        pq.write_table(t, p)
        return p

    def test_a_finished_past_year_is_covered(self, tmp_path):
        from data.build.acquire import drought

        assert drought._covers(2015, self._write(tmp_path, 2015, "2015-12-31"))

    def test_a_partial_current_year_is_not_covered(self, tmp_path):
        """The failure this guards: August data for a year that has not ended."""
        from data.build.acquire import drought

        this = pd.Timestamp.today().year
        stale = (pd.Timestamp.today() - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        assert not drought._covers(this, self._write(tmp_path, this, stale))

    def test_a_past_year_stopping_early_is_not_covered(self, tmp_path):
        from data.build.acquire import drought

        assert not drought._covers(2015, self._write(tmp_path, 2015, "2015-06-30"))

    def test_a_missing_file_is_not_covered(self, tmp_path):
        from data.build.acquire import drought

        assert not drought._covers(2015, tmp_path / "absent.parquet")


class TestDroughtHorizon:
    """The horizon is shared by the fetch guard and the verify check, so one number cannot drift from the other.

    Two pentads of slack, not one: the cadence is five days AND publication lags it, so a store holding data to the 23rd on the 31st is current. A one-pentad horizon called that frozen — which would refetch the year every pass and make the check cry wolf, and a check that cries wolf gets ignored.
    """

    def test_the_horizon_allows_two_pentads_of_lag(self):
        import datetime as dt

        from data.build.acquire import drought

        this = dt.date.today().year
        assert drought._horizon(this) == dt.date.today() - dt.timedelta(days=10)

    def test_a_past_year_is_bounded_by_its_own_end(self):
        import datetime as dt

        from data.build.acquire import drought

        assert drought._horizon(2015) == dt.date(2015, 12, 31)

    def test_a_store_eight_days_behind_is_still_current(self):
        """The exact case that produced a false failure: 2026-08-23 held on 2026-08-31."""
        import datetime as dt

        import pyarrow as pa
        import pyarrow.parquet as pq

        from data.build.acquire import drought

        this = dt.date.today().year
        recent = dt.date.today() - dt.timedelta(days=8)
        import tempfile, pathlib
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / f"gridmet_pentad_{this}.parquet"
        pq.write_table(pa.table({"cell_id": [1], "date": [recent]}), p)
        assert drought._covers(this, p)

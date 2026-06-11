"""Pytest suite for the writer/core — no GUI, no real reference file needed.

Run from the project root with:  pytest   (or  python -m palm_static_generator.tests.test_writer)

Synthesises a minimal PALM-3D-output-like reference file and checks the static
driver's structure, rasterization, the new water / terrain / connected-id
behaviour, validation, and the lat/lon -> UTM helper.
"""

import os
import sys
import tempfile

import numpy as np
import pytest
from netCDF4 import Dataset

from palm_static_generator.core import geo, palm_types, writer


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def make_reference(path, nx=39, ny=39, nz=40, dx=2.0, dy=2.0, names=("zu_3d", "zw_3d")):
    """Create a tiny PALM-output-like file with vertical-grid + x/y variables."""
    ds = Dataset(path, "w", format="NETCDF4")
    ds.createDimension("x", nx + 1)
    ds.createDimension("y", ny + 1)
    ds.createDimension(names[0], nz)
    ds.createDimension(names[1], nz)
    x = ds.createVariable("x", "f4", ("x",))
    y = ds.createVariable("y", "f4", ("y",))
    zu = ds.createVariable(names[0], "f4", (names[0],))
    zw = ds.createVariable(names[1], "f4", (names[1],))
    x[:] = np.arange(nx + 1) * dx + 0.5 * dx
    y[:] = np.arange(ny + 1) * dy + 0.5 * dy
    zu[:] = (np.arange(nz) + 0.5) * dx
    zw[:] = (np.arange(nz) + 1.0) * dx
    ds.close()


def make_config(ref_path):
    return {
        "reference_file": ref_path,
        "domain": {"nx": 39, "ny": 39, "dx": 2.0, "dy": 2.0},
        "output": {"filename": "unused"},
        "global_attributes": {
            "title": "test", "author": "tester", "institution": "x",
            "origin_lat": 48.85, "origin_lon": 2.35, "origin_x": 452000.0,
            "origin_y": 5411600.0, "origin_z": 35.0,
            "origin_time": "2023-07-15 06:00:00 +00", "rotation_angle": 0.0,
        },
        "defaults": {
            "background_type": "pavement", "pavement_type": 2, "soil_type": 2,
            "soil_temperature": [300.0, 295.0, 290.0, 290.0, 290.0, 290.0, 290.0, 290.0],
            "soil_moisture": 0.05, "deep_soil_temperature": 290.0,
        },
    }


def make_zones():
    return [
        {"type": "building", "label": "B1", "i0": 6, "i1": 20, "j0": 20, "j1": 34,
         "building_height": 10.0, "building_type": 2},
        {"type": "vegetation", "label": "Park", "i0": 6, "i1": 26, "j0": 6, "j1": 12,
         "vegetation_type": 4, "soil_type": 2,
         "lad": list(palm_types.DEFAULT_LAD_PROFILE)},
        {"type": "pavement", "label": "Plaza", "i0": 28, "i1": 34, "j0": 6, "j1": 18,
         "pavement_type": 1, "soil_type": 2},
        {"type": "soil", "label": "SoilPatch", "i0": 6, "i1": 12, "j0": 6, "j1": 9,
         "soil_type": 3, "soil_moisture": 0.20,
         "soil_temperature": [288.0] * 8, "deep_soil_temperature": 285.0},
    ]


@pytest.fixture()
def ref(tmp_path):
    p = str(tmp_path / "INIT_3d.001.nc")
    make_reference(p)
    return p


# --------------------------------------------------------------------------- #
# Reference reading
# --------------------------------------------------------------------------- #
def test_read_reference_grid_3d(ref):
    grid = writer.read_reference_grid(ref)
    assert grid["nx"] == 39 and grid["ny"] == 39
    assert abs(grid["dx"] - 2.0) < 1e-6 and abs(grid["dy"] - 2.0) < 1e-6
    assert grid["zu"].shape == (40,)


def test_read_reference_grid_2d_slice_names(tmp_path):
    p = str(tmp_path / "slice.nc")
    make_reference(p, names=("zu", "zw"))     # 2-D-slice naming
    grid = writer.read_reference_grid(p)
    assert grid["zu"].shape == (40,) and grid["zw"].shape == (40,)


# --------------------------------------------------------------------------- #
# Structure / rasterization
# --------------------------------------------------------------------------- #
EXPECTED_VARS = {
    "x", "y", "z", "zlad", "nsurface_fraction", "nalbedo_pars", "zsoil",
    "vegetation_type", "pavement_type", "soil_type", "water_type",
    "building_type", "building_id", "buildings_2d", "buildings_3d", "zt",
    "surface_fraction", "lad", "albedo_pars", "soil_temperature",
    "soil_moisture", "deep_soil_temperature",
}


def test_structure_and_rasterization(ref, tmp_path):
    out = str(tmp_path / "static")
    grid = writer.read_reference_grid(ref)
    writer.write_static_driver(make_config(ref), make_zones(),
                               grid["zu"], grid["zw"], out)
    ds = Dataset(out)
    try:
        assert not (EXPECTED_VARS - set(ds.variables))
        assert len(ds.dimensions["x"]) == 40 and len(ds.dimensions["zsoil"]) == 8
        assert ds.variables["vegetation_type"].dtype == np.int8
        assert ds.variables["building_id"].dtype == np.int32
        assert ds.variables["buildings_2d"].dtype == np.float32
        assert ds.variables["vegetation_type"]._FillValue == -127
        assert ds.Conventions == "CF-1.7" and ds.previous_run == ref

        b2d = ds.variables["buildings_2d"][:]
        bid = ds.variables["building_id"][:]
        sf = ds.variables["surface_fraction"][:]
        assert np.isclose(b2d[25, 10], 10.0) and bid[25, 10] == 1
        assert np.allclose(sf[:, 25, 10], 0.0)

        b3d = ds.variables["buildings_3d"][:]
        expected = ((10.0 > grid["zw"]) & (10.0 > 0)).astype(np.int8)
        assert np.array_equal(b3d[:, 25, 10], expected)

        lad = ds.variables["lad"][:]
        assert np.all(lad[:, 8, 10] != -9999.0) and np.isclose(sf[0, 8, 10], 1.0)
        assert ds.variables["pavement_type"][:][10, 30] == 1
        sm = ds.variables["soil_moisture"][:]
        assert np.isclose(sm[0, 7, 8], 0.20) and np.isclose(sm[0, 0, 0], 0.05)
        assert ds.variables["soil_type"][:][7, 8] == 3
        assert np.allclose(ds.variables["zt"][:], 0.0)
    finally:
        ds.close()


def test_water_and_terrain(ref):
    grid = writer.read_reference_grid(ref)
    cfg = make_config(ref)
    zones = [
        {"type": "water", "label": "lake", "i0": 2, "i1": 8, "j0": 2, "j1": 8,
         "water_type": 1},
        {"type": "terrain", "label": "hill", "i0": 0, "i1": 40, "j0": 0, "j1": 40,
         "terrain_height": 5.0},
    ]
    f = writer.build_fields(cfg, zones, grid["zu"], grid["zw"])
    assert (f["water_type"] == 1).sum() == 36
    assert np.allclose(f["surface_fraction"][2][f["water_type"] == 1], 1.0)
    assert np.allclose(f["zt"], 5.0)
    writer.validate_fields(f)   # must not raise


def test_connected_component_ids(ref):
    grid = writer.read_reference_grid(ref)
    cfg = make_config(ref)
    zones = [  # two adjacent zones merge to one id; a third is separate
        {"type": "building", "label": "A", "i0": 2, "i1": 6, "j0": 2, "j1": 8,
         "building_height": 10.0, "building_type": 2},
        {"type": "building", "label": "B", "i0": 6, "i1": 10, "j0": 2, "j1": 8,
         "building_height": 8.0, "building_type": 2},
        {"type": "building", "label": "C", "i0": 30, "i1": 36, "j0": 30, "j1": 36,
         "building_height": 12.0, "building_type": 2},
    ]
    f = writer.build_fields(cfg, zones, grid["zu"], grid["zw"])
    ids = np.unique(f["building_id"][f["building_id"] > 0])
    assert len(ids) == 2


def test_label_connected_matches_scipy():
    ndimage = pytest.importorskip("scipy.ndimage")
    rng = np.random.default_rng(1)
    for _ in range(10):
        m = rng.random((25, 25)) > 0.5
        mine, _ = writer._label_connected(m)
        sci, _ = ndimage.label(m)
        assert len(np.unique(mine[m])) == len(np.unique(sci[m]))


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_validate_config_rejects_bad_profile(ref):
    cfg = make_config(ref)
    cfg["defaults"]["soil_temperature"] = [300.0, 295.0]      # wrong length
    with pytest.raises(ValueError):
        writer.validate_config(cfg)


def test_validate_zones_rejects_out_of_range(ref):
    with pytest.raises(ValueError):
        writer.validate_zones(
            [{"type": "building", "i0": 0, "i1": 999, "j0": 0, "j1": 5,
              "building_height": 5.0, "building_type": 2}], 39, 39)


# --------------------------------------------------------------------------- #
# Geo (lat/lon -> UTM)
# --------------------------------------------------------------------------- #
def test_utm_paris():
    res = geo.latlon_to_utm(48.8534, 2.3488)
    assert res["zone"] == 31 and res["hemisphere"] == "N"
    assert abs(res["easting"] - 452230.0) < 5.0
    assert abs(res["northing"] - 5411363.0) < 5.0


def test_utm_matches_pyproj():
    pyproj = pytest.importorskip("pyproj")
    pts = [(48.8534, 2.3488), (-33.8688, 151.2093), (40.7128, -74.0060)]
    for lat, lon in pts:
        r = geo.latlon_to_utm(lat, lon)
        epsg = (32600 if r["hemisphere"] == "N" else 32700) + r["zone"]
        tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:{}".format(epsg),
                                         always_xy=True)
        ex, ny = tr.transform(lon, lat)
        assert abs(ex - r["easting"]) < 0.01 and abs(ny - r["northing"]) < 0.01


if __name__ == "__main__":  # allow `python -m ...tests.test_writer`
    sys.exit(pytest.main([os.path.abspath(__file__), "-q"]))

"""Headless smoke test for the writer — no GUI, no real reference file needed.

Run from the project root with:  python -m palm_static_generator.tests.test_writer

It synthesises a minimal PALM-3D-output-like reference file, builds a small
scene (background pavement + a building, a tree patch, a pavement patch and a
soil override), writes the static driver, and re-opens it to assert the
structure matches what ``static_driver_soil.py`` produces.
"""

import os
import tempfile

import numpy as np
from netCDF4 import Dataset

from palm_static_generator.core import palm_types, writer


def make_reference(path, nx=39, ny=39, nz=40, dx=2.0, dy=2.0):
    """Create a tiny PALM-3D-output-like file with zu_3d / zw_3d / x / y."""
    ds = Dataset(path, "w", format="NETCDF4")
    ds.createDimension("x", nx + 1)
    ds.createDimension("y", ny + 1)
    ds.createDimension("zu_3d", nz)
    ds.createDimension("zw_3d", nz)
    x = ds.createVariable("x", "f4", ("x",))
    y = ds.createVariable("y", "f4", ("y",))
    zu = ds.createVariable("zu_3d", "f4", ("zu_3d",))
    zw = ds.createVariable("zw_3d", "f4", ("zw_3d",))
    x[:] = np.arange(nx + 1) * dx + 0.5 * dx
    y[:] = np.arange(ny + 1) * dy + 0.5 * dy
    # simple constant-dz vertical grid
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
            "background_type": "pavement",
            "pavement_type": 2,
            "soil_type": 2,
            "soil_temperature": [300.0, 295.0, 290.0, 290.0, 290.0, 290.0, 290.0, 290.0],
            "soil_moisture": 0.05,
            "deep_soil_temperature": 290.0,
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


EXPECTED_VARS = {
    "x", "y", "z", "zlad", "nsurface_fraction", "nalbedo_pars", "zsoil",
    "vegetation_type", "pavement_type", "soil_type", "building_type",
    "building_id", "buildings_2d", "buildings_3d", "surface_fraction",
    "lad", "albedo_pars", "soil_temperature", "soil_moisture",
    "deep_soil_temperature",
}


def main():
    tmp = tempfile.mkdtemp(prefix="palm_test_")
    ref = os.path.join(tmp, "INIT_3d.001.nc")
    out = os.path.join(tmp, "test_static")

    make_reference(ref)
    grid = writer.read_reference_grid(ref)
    assert grid["nx"] == 39 and grid["ny"] == 39, "domain inference failed"
    assert abs(grid["dx"] - 2.0) < 1e-6 and abs(grid["dy"] - 2.0) < 1e-6

    config = make_config(ref)
    zones = make_zones()
    writer.write_static_driver(config, zones, grid["zu"], grid["zw"], out)

    ds = Dataset(out)
    try:
        missing = EXPECTED_VARS - set(ds.variables)
        assert not missing, "missing variables: {}".format(missing)

        # dimensions
        assert len(ds.dimensions["x"]) == 40
        assert len(ds.dimensions["y"]) == 40
        assert len(ds.dimensions["z"]) == len(grid["zu"])
        assert len(ds.dimensions["zlad"]) == 7
        assert len(ds.dimensions["zsoil"]) == 8
        assert len(ds.dimensions["nsurface_fraction"]) == 3

        # dtypes / fill values on a representative set
        assert ds.variables["vegetation_type"].dtype == np.int8
        assert ds.variables["building_id"].dtype == np.int32
        assert ds.variables["buildings_2d"].dtype == np.float32
        assert ds.variables["buildings_3d"].dtype == np.int8
        assert ds.variables["vegetation_type"]._FillValue == -127
        assert ds.variables["building_id"]._FillValue == -9999
        assert abs(float(ds.variables["buildings_2d"]._FillValue) + 9999.0) < 1e-3

        # global attributes
        assert ds.Conventions == "CF-1.7"
        assert ds.previous_run == ref

        # building present, height correct, surface_fraction zero there
        b2d = ds.variables["buildings_2d"][:]
        bid = ds.variables["building_id"][:]
        assert np.isclose(b2d[25, 10], 10.0), "building height wrong"
        assert bid[25, 10] == 1, "building id wrong"
        sf = ds.variables["surface_fraction"][:]
        assert np.allclose(sf[:, 25, 10], 0.0), "building surface_fraction not zero"

        # buildings_3d: column under a 10 m building, dz=2 → flagged up to ~5 faces
        b3d = ds.variables["buildings_3d"][:]
        zw = grid["zw"]
        expected_col = ((10.0 > zw) & (10.0 > 0)).astype(np.int8)
        assert np.array_equal(b3d[:, 25, 10], expected_col), "buildings_3d logic wrong"

        # vegetation patch: lad present, surface_fraction veg=1
        lad = ds.variables["lad"][:]
        assert np.all(lad[:, 8, 10] != -9999.0), "lad missing on tree cell"
        assert np.isclose(sf[0, 8, 10], 1.0), "veg surface_fraction wrong"

        # pavement patch type 1
        pav = ds.variables["pavement_type"][:]
        assert pav[10, 30] == 1, "pavement patch type wrong"
        assert np.isclose(sf[1, 10, 30], 1.0), "pav surface_fraction wrong"

        # soil override: moisture 0.20 inside patch, 0.05 outside
        sm = ds.variables["soil_moisture"][:]
        assert np.isclose(sm[0, 7, 8], 0.20), "soil override not applied"
        assert np.isclose(sm[0, 0, 0], 0.05), "background soil moisture wrong"
        st = ds.variables["soil_type"][:]
        assert st[7, 8] == 3, "soil override soil_type not applied to surface cell"
    finally:
        ds.close()

    print("\nALL TESTS PASSED")
    print("Output written to:", out)


if __name__ == "__main__":
    main()

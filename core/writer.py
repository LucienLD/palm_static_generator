"""NetCDF writer for PALM static driver files.

This module turns a configuration dictionary and an ordered list of *zone*
dictionaries (as produced by the GUI) into a PALM-compatible static-driver
NetCDF4 file whose structure is identical to the reference
``static_driver_soil.py``: same dimensions, variable names, dtypes, fill
values and CF-1.7 attributes.

The module is deliberately free of any GUI dependency so it can be imported and
driven from a batch script::

    from palm_static_generator.core import writer
    grid = writer.read_reference_grid("INIT_3d.001.nc")
    writer.write_static_driver(config, zones, grid["zu"], grid["zw"])

or run directly::

    python -m palm_static_generator.core.writer config.yaml zones.yaml -o out

Zone dictionary schema
----------------------
Every zone has the common keys ``type`` (``"building" | "vegetation" |
"pavement" | "soil"``), ``label`` and the integer slice bounds ``i0, i1, j0,
j1`` (``i1`` / ``j1`` exclusive, matching ``numpy`` slice semantics).  Per
type:

* building   -> ``building_height`` (float, m), ``building_type`` (code)
* vegetation -> ``vegetation_type`` (code), ``soil_type`` (code),
  ``lad`` (list of 7 floats)
* pavement   -> ``pavement_type`` (code), ``soil_type`` (code)
* soil       -> ``soil_type`` (code), ``soil_temperature`` (list of 8 floats),
  ``soil_moisture`` (float), ``deep_soil_temperature`` (float)
"""

import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from netCDF4 import Dataset

try:  # work as a package or via direct ``python core/writer.py`` execution
    from . import palm_types
    from .palm_types import (
        ALBEDO_DEFAULTS,
        BARE_SOIL_VEGETATION_TYPE,
        N_ALBEDO_PARS,
        N_SURFACE_FRACTION,
        N_ZLAD,
        ZSOIL_DZ,
    )
except ImportError:  # pragma: no cover - direct ``python writer.py`` execution
    import palm_types
    from palm_types import (
        ALBEDO_DEFAULTS,
        BARE_SOIL_VEGETATION_TYPE,
        N_ALBEDO_PARS,
        N_SURFACE_FRACTION,
        N_ZLAD,
        ZSOIL_DZ,
    )

# --------------------------------------------------------------------------- #
# Fill values (identical to the reference script)
# --------------------------------------------------------------------------- #
FILL_I1 = np.int8(-127)
FILL_I4 = np.int32(-9999)
FILL_F4 = np.float32(-9999.0)


# --------------------------------------------------------------------------- #
# Reference-file reading
# --------------------------------------------------------------------------- #
def read_reference_grid(path: str) -> Dict[str, object]:
    """Read the vertical grid and domain size from a PALM 3D output file.

    Parameters
    ----------
    path:
        Path to a PALM 3D output NetCDF (e.g. ``INIT_3d.001.nc`` or any
        ``*_3d*.nc``).

    Returns
    -------
    dict
        Keys: ``zu`` (cell-centre heights), ``zw`` (cell-face heights),
        ``nx``, ``ny`` (number of cells minus one), ``dx``, ``dy`` and
        ``path``.

    Raises
    ------
    KeyError
        If ``zu_3d`` / ``zw_3d`` are not present in the file.
    """
    ds = Dataset(path)
    try:
        if "zu_3d" not in ds.variables or "zw_3d" not in ds.variables:
            raise KeyError(
                "Reference file '{}' does not contain zu_3d / zw_3d; "
                "it does not look like a PALM 3D output file.".format(path)
            )
        zu = np.asarray(ds.variables["zu_3d"][:], dtype=np.float64)
        zw = np.asarray(ds.variables["zw_3d"][:], dtype=np.float64)

        nx, dx = _axis_size_and_spacing(ds, "x")
        ny, dy = _axis_size_and_spacing(ds, "y")
    finally:
        ds.close()

    return {
        "zu": zu,
        "zw": zw,
        "nx": nx,
        "ny": ny,
        "dx": dx,
        "dy": dy,
        "path": path,
    }


def _axis_size_and_spacing(ds: Dataset, name: str) -> Tuple[int, float]:
    """Infer ``n<axis>`` (cells - 1) and ``d<axis>`` spacing from a 3D file."""
    if name in ds.variables:
        coord = np.asarray(ds.variables[name][:], dtype=np.float64)
        n = len(coord) - 1
        d = float(coord[1] - coord[0]) if len(coord) > 1 else 0.0
        return n, d
    if name in ds.dimensions:
        return len(ds.dimensions[name]) - 1, 0.0
    raise KeyError("Reference file has no '{}' coordinate or dimension".format(name))


# --------------------------------------------------------------------------- #
# Rasterization
# --------------------------------------------------------------------------- #
def build_fields(
    config: Dict,
    zones: Sequence[Dict],
    zu: np.ndarray,
    zw: np.ndarray,
) -> Dict[str, np.ndarray]:
    """Rasterize the config + zone list into the static-driver field arrays.

    Returns a dict of ``numpy`` arrays keyed by NetCDF variable name, plus the
    private bookkeeping mask ``_tree_mask`` (cells that must carry an LAD
    profile).  The arrays are *not* yet written to disk; pass the result to
    :func:`validate_fields` and then :func:`write_static_driver`.
    """
    domain = config["domain"]
    nx, ny = int(domain["nx"]), int(domain["ny"])
    nx1, ny1 = nx + 1, ny + 1
    nz = len(zu)
    n_zsoil = len(ZSOIL_DZ)
    shape2d = (ny1, nx1)

    defaults = config["defaults"]

    # --- allocate, all to fill value --------------------------------------
    veg = np.full(shape2d, FILL_I1, dtype=np.int8)
    pav = np.full(shape2d, FILL_I1, dtype=np.int8)
    soil = np.full(shape2d, FILL_I1, dtype=np.int8)
    btype = np.full(shape2d, FILL_I1, dtype=np.int8)
    bid = np.full(shape2d, FILL_I4, dtype=np.int32)
    b2d = np.full(shape2d, FILL_F4, dtype=np.float32)
    sfrac = np.zeros((N_SURFACE_FRACTION,) + shape2d, dtype=np.float32)
    lad = np.full((N_ZLAD,) + shape2d, FILL_F4, dtype=np.float32)
    albedo = np.full((N_ALBEDO_PARS,) + shape2d, FILL_F4, dtype=np.float32)
    tree_mask = np.zeros(shape2d, dtype=bool)

    # --- background -------------------------------------------------------
    background = str(defaults.get("background_type", "pavement")).lower()
    if "soil" in background:  # "bare soil"
        veg[:, :] = int(BARE_SOIL_VEGETATION_TYPE)
        soil[:, :] = int(defaults["soil_type"])
        sfrac[0, :, :] = 1.0
        _set_albedo(albedo, np.s_[:, :], ALBEDO_DEFAULTS["vegetation"])
    else:  # "pavement"
        pav[:, :] = int(defaults["pavement_type"])
        soil[:, :] = int(defaults["soil_type"])
        sfrac[1, :, :] = 1.0
        _set_albedo(albedo, np.s_[:, :], ALBEDO_DEFAULTS["pavement"])

    # --- surface zones (painter's algorithm: last in list wins) -----------
    next_building_id = 1
    for zone in zones:
        ztype = zone["type"]
        if ztype == "soil":
            continue  # soil overrides handled in a separate pass
        i0, i1, j0, j1 = _bounds(zone)
        sl = (slice(j0, j1), slice(i0, i1))

        # clear every surface classification under this footprint
        veg[sl] = FILL_I1
        pav[sl] = FILL_I1
        soil[sl] = FILL_I1
        btype[sl] = FILL_I1
        bid[sl] = FILL_I4
        b2d[sl] = FILL_F4
        lad[:, j0:j1, i0:i1] = FILL_F4
        sfrac[:, j0:j1, i0:i1] = 0.0
        tree_mask[sl] = False

        if ztype == "building":
            bid[sl] = next_building_id
            next_building_id += 1
            b2d[sl] = float(zone["building_height"])
            btype[sl] = int(zone["building_type"])
            # surface_fraction stays [0, 0, 0]; USM handles building cells
            _set_albedo(albedo, sl, ALBEDO_DEFAULTS["building"])
        elif ztype == "vegetation":
            veg[sl] = int(zone["vegetation_type"])
            soil[sl] = int(zone["soil_type"])
            sfrac[0][sl] = 1.0
            profile = zone["lad"]
            for k in range(N_ZLAD):
                lad[k][sl] = float(profile[k])
            tree_mask[sl] = True
            _set_albedo(albedo, sl, ALBEDO_DEFAULTS["vegetation"])
        elif ztype == "pavement":
            pav[sl] = int(zone["pavement_type"])
            soil[sl] = int(zone["soil_type"])
            sfrac[1][sl] = 1.0
            _set_albedo(albedo, sl, ALBEDO_DEFAULTS["pavement"])
        else:
            raise ValueError("Unknown zone type: {!r}".format(ztype))

    # --- soil initial conditions ------------------------------------------
    # Filled over the ENTIRE domain (matching the reference): leaving fill
    # values on non-building cells triggers PALM's LSM0046 (moisture < 0).
    soil_T = np.empty((n_zsoil,) + shape2d, dtype=np.float32)
    soil_M = np.empty((n_zsoil,) + shape2d, dtype=np.float32)
    deep_T = np.empty(shape2d, dtype=np.float32)

    T_profile = list(defaults["soil_temperature"])
    for k in range(n_zsoil):
        soil_T[k, :, :] = float(T_profile[k])
    soil_M[:, :, :] = float(defaults["soil_moisture"])
    deep_T[:, :] = float(defaults["deep_soil_temperature"])

    # soil-override zones: overwrite the soil initial fields; do not change the
    # surface classification.  ``soil_type`` is applied only where a surface
    # (vegetation or pavement) already exists, to keep DRV0023 consistent.
    surface_mask = (veg != FILL_I1) | (pav != FILL_I1)
    for zone in zones:
        if zone["type"] != "soil":
            continue
        i0, i1, j0, j1 = _bounds(zone)
        sl = (slice(j0, j1), slice(i0, i1))
        profile = zone["soil_temperature"]
        for k in range(n_zsoil):
            soil_T[k][sl] = float(profile[k])
        soil_M[:, j0:j1, i0:i1] = float(zone["soil_moisture"])
        deep_T[sl] = float(zone["deep_soil_temperature"])
        region_surface = np.zeros(shape2d, dtype=bool)
        region_surface[sl] = True
        region_surface &= surface_mask
        soil[region_surface] = int(zone["soil_type"])

    # --- 3D building flag --------------------------------------------------
    b3d = np.zeros((nz,) + shape2d, dtype=np.int8)
    for k in range(nz):
        b3d[k, :, :] = np.where((b2d > zw[k]) & (b2d > 0.0), 1, 0).astype(np.int8)

    return {
        "vegetation_type": veg,
        "pavement_type": pav,
        "soil_type": soil,
        "building_type": btype,
        "building_id": bid,
        "buildings_2d": b2d,
        "buildings_3d": b3d,
        "surface_fraction": sfrac,
        "lad": lad,
        "albedo_pars": albedo,
        "soil_temperature": soil_T,
        "soil_moisture": soil_M,
        "deep_soil_temperature": deep_T,
        "_tree_mask": tree_mask,
    }


def _bounds(zone: Dict) -> Tuple[int, int, int, int]:
    """Return integer ``(i0, i1, j0, j1)`` slice bounds for a zone."""
    return int(zone["i0"]), int(zone["i1"]), int(zone["j0"]), int(zone["j1"])


def _set_albedo(albedo: np.ndarray, sl, values: Sequence[float]) -> None:
    """Assign the 7 albedo-parameter values over a 2D slice of ``albedo``."""
    for idx in range(N_ALBEDO_PARS):
        albedo[idx][sl] = float(values[idx])


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_fields(fields: Dict[str, np.ndarray]) -> str:
    """Validate rasterized fields; raise ``ValueError`` on any failure.

    On success returns a multi-line human-readable report of the per-category
    cell counts.
    """
    veg = fields["vegetation_type"]
    pav = fields["pavement_type"]
    soil = fields["soil_type"]
    bid = fields["building_id"]
    b2d = fields["buildings_2d"]
    sfrac = fields["surface_fraction"]
    lad = fields["lad"]
    tree_mask = fields["_tree_mask"]

    building_mask = bid != FILL_I4
    veg_mask = veg != FILL_I1
    pav_mask = pav != FILL_I1

    n_cells = veg.size
    n_building = int(building_mask.sum())
    n_veg = int(veg_mask.sum())
    n_pav = int(pav_mask.sum())

    # 1. exactly one surface category per cell
    category_count = (
        building_mask.astype(int) + veg_mask.astype(int) + pav_mask.astype(int)
    )
    n_uncovered = int((category_count == 0).sum())
    n_overlap = int((category_count > 1).sum())
    if n_uncovered:
        raise ValueError(
            "{} cell(s) have no surface category (building/vegetation/pavement)."
            .format(n_uncovered)
        )
    if n_overlap:
        raise ValueError(
            "{} cell(s) belong to more than one surface category.".format(n_overlap)
        )
    if n_building + n_veg + n_pav != n_cells:
        raise ValueError(
            "Category partition error: {} + {} + {} != {}".format(
                n_building, n_veg, n_pav, n_cells
            )
        )

    # 2. no cell has both vegetation_type and pavement_type
    if np.any(veg_mask & pav_mask):
        raise ValueError("Some cells have both vegetation_type and pavement_type set.")

    # 3. surface_fraction row sum: 0 on building cells, 1 elsewhere
    ssum = sfrac.sum(axis=0)
    if not np.allclose(ssum[building_mask], 0.0, atol=1e-5):
        raise ValueError("surface_fraction must sum to 0 on building cells.")
    other = ~building_mask
    if not np.allclose(ssum[other], 1.0, atol=1e-5):
        raise ValueError("surface_fraction must sum to 1 on non-building cells.")

    # 4. soil_type set wherever vegetation_type or pavement_type is set (DRV0023)
    need_soil = veg_mask | pav_mask
    if np.any(soil[need_soil] == FILL_I1):
        raise ValueError(
            "soil_type missing on some vegetation/pavement cells (PALM DRV0023)."
        )

    # 5. every building cell has building_id > 0 and buildings_2d > 0
    if np.any(bid[building_mask] <= 0):
        raise ValueError("Some building cells have building_id <= 0.")
    if np.any(b2d[building_mask] <= 0.0):
        raise ValueError("Some building cells have buildings_2d <= 0.")

    # 6. lad present on every tree (vegetation-zone) cell
    if np.any(tree_mask) and np.any(lad[:, tree_mask] == FILL_F4):
        raise ValueError("lad missing on some vegetation-zone cells.")

    report = [
        "Validation OK.",
        "  Buildings  : {:>8} cells".format(n_building),
        "  Vegetation : {:>8} cells".format(n_veg),
        "  Pavement   : {:>8} cells".format(n_pav),
        "  Total      : {:>8} / {} cells".format(
            n_building + n_veg + n_pav, n_cells
        ),
        "  Tree (LAD) : {:>8} cells".format(int(tree_mask.sum())),
    ]
    return "\n".join(report)


# --------------------------------------------------------------------------- #
# NetCDF writing
# --------------------------------------------------------------------------- #
def write_static_driver(
    config: Dict,
    zones: Sequence[Dict],
    zu: np.ndarray,
    zw: np.ndarray,
    output_path: Optional[str] = None,
) -> str:
    """Validate and write a PALM static driver NetCDF file.

    Parameters
    ----------
    config:
        Configuration dictionary (see ``config.yaml``).
    zones:
        Ordered list of zone dictionaries.
    zu, zw:
        Vertical cell-centre / cell-face arrays read from the reference file.
    output_path:
        Destination path.  Defaults to ``config["output"]["filename"]``.

    Returns
    -------
    str
        The path that was written.
    """
    if output_path is None:
        output_path = config["output"]["filename"]

    zu = np.asarray(zu, dtype=np.float64)
    zw = np.asarray(zw, dtype=np.float64)

    fields = build_fields(config, zones, zu, zw)
    report = validate_fields(fields)
    print(report)

    domain = config["domain"]
    nx, ny = int(domain["nx"]), int(domain["ny"])
    dx, dy = float(domain["dx"]), float(domain["dy"])
    nz = len(zu)

    nc = Dataset(output_path, "w", format="NETCDF4")
    try:
        _write_global_attributes(nc, config)
        _write_coordinates(nc, nx, ny, dx, dy, zu, zw)
        _write_data_variables(nc, fields)
    finally:
        nc.close()

    print("Wrote PALM static driver -> {}".format(output_path))
    return output_path


def _write_global_attributes(nc: Dataset, config: Dict) -> None:
    """Write CF-1.7 global attributes from the config dictionary."""
    ga = config.get("global_attributes", {})

    nc.Conventions = "CF-1.7"
    nc.title = str(ga.get("title", ""))
    nc.author = str(ga.get("author", ""))
    nc.institution = str(ga.get("institution", ""))
    nc.creation_date = (
        datetime.datetime.utcnow().strftime("%y-%m-%d %H:%M:%S") + " +00"
    )
    nc.origin_lat = float(ga.get("origin_lat", 0.0))
    nc.origin_lon = float(ga.get("origin_lon", 0.0))
    nc.origin_x = float(ga.get("origin_x", 0.0))
    nc.origin_y = float(ga.get("origin_y", 0.0))
    nc.origin_z = float(ga.get("origin_z", 0.0))
    nc.origin_time = str(ga.get("origin_time", ""))
    nc.rotation_angle = float(ga.get("rotation_angle", 0.0))
    # Path to the previous run whose vertical grid was reused.
    nc.previous_run = str(config.get("reference_file", ""))


def _write_coordinates(
    nc: Dataset,
    nx: int,
    ny: int,
    dx: float,
    dy: float,
    zu: np.ndarray,
    zw: np.ndarray,
) -> None:
    """Create all dimensions and coordinate variables."""
    nx1, ny1 = nx + 1, ny + 1

    nc.createDimension("x", nx1)
    x = nc.createVariable("x", "f4", ("x",))
    x.long_name = "distance to origin in x-direction"
    x.units = "m"
    x.axis = "X"
    x[:] = np.arange(nx1) * dx + 0.5 * dx

    nc.createDimension("y", ny1)
    y = nc.createVariable("y", "f4", ("y",))
    y.long_name = "distance to origin in y-direction"
    y.units = "m"
    y.axis = "Y"
    y[:] = np.arange(ny1) * dy + 0.5 * dy

    nc.createDimension("z", len(zu))
    z = nc.createVariable("z", "f4", ("z",))
    z.long_name = "height above origin"
    z.units = "m"
    z.axis = "Z"
    z.positive = "up"
    z[:] = zu

    nc.createDimension("zlad", N_ZLAD)
    zlad = nc.createVariable("zlad", "f4", ("zlad",))
    zlad.long_name = "height above ground"
    zlad.units = "m"
    zlad.axis = "Z"
    zlad.positive = "up"
    zlad[:] = zu[0:N_ZLAD]

    nc.createDimension("nsurface_fraction", N_SURFACE_FRACTION)
    nsf = nc.createVariable("nsurface_fraction", "i4", ("nsurface_fraction",))
    nsf[:] = np.arange(N_SURFACE_FRACTION)

    nc.createDimension("nalbedo_pars", N_ALBEDO_PARS)
    nap = nc.createVariable("nalbedo_pars", "i4", ("nalbedo_pars",))
    nap[:] = np.arange(N_ALBEDO_PARS)

    dz_soil = np.array(ZSOIL_DZ, dtype=np.float64)
    zsoil_centers = np.round(np.cumsum(dz_soil) - dz_soil / 2.0, 4)
    nc.createDimension("zsoil", len(dz_soil))
    zsoil = nc.createVariable("zsoil", "f4", ("zsoil",))
    zsoil.long_name = "depth in the soil"
    zsoil.units = "m"
    zsoil.axis = "Z"
    zsoil.positive = "down"
    zsoil[:] = zsoil_centers


def _write_data_variables(nc: Dataset, fields: Dict[str, np.ndarray]) -> None:
    """Create and fill all static-driver data variables."""
    v = nc.createVariable("vegetation_type", "i1", ("y", "x"), fill_value=-127)
    v.long_name = "vegetation type classification"
    v.units = "1"
    v[:, :] = fields["vegetation_type"]

    v = nc.createVariable("pavement_type", "i1", ("y", "x"), fill_value=-127)
    v.long_name = "pavement type classification"
    v.units = "1"
    v[:, :] = fields["pavement_type"]

    v = nc.createVariable("soil_type", "i1", ("y", "x"), fill_value=-127)
    v.long_name = "soil type classification"
    v.units = "1"
    v.lod = np.int32(1)
    v[:, :] = fields["soil_type"]

    v = nc.createVariable("building_type", "i1", ("y", "x"), fill_value=-127)
    v.long_name = "building type classification"
    v.units = "1"
    v[:, :] = fields["building_type"]

    v = nc.createVariable("building_id", "i4", ("y", "x"), fill_value=-9999)
    v.long_name = "building id number"
    v.units = "1"
    v[:, :] = fields["building_id"]

    v = nc.createVariable("buildings_2d", "f4", ("y", "x"), fill_value=-9999.0)
    v.long_name = "building height"
    v.units = "m"
    v.lod = np.int32(1)
    v[:, :] = fields["buildings_2d"]

    v = nc.createVariable("buildings_3d", "i1", ("z", "y", "x"), fill_value=-127)
    v.long_name = "building flag"
    v.units = "1"
    v.lod = np.int32(2)
    v[:, :, :] = fields["buildings_3d"]

    v = nc.createVariable(
        "surface_fraction", "f4", ("nsurface_fraction", "y", "x"), fill_value=-9999.0
    )
    v.long_name = "surface fraction"
    v.units = "1"
    v[:, :, :] = fields["surface_fraction"]

    v = nc.createVariable("lad", "f4", ("zlad", "y", "x"), fill_value=-9999.0)
    v.long_name = "leaf area density"
    v.units = "m2 m-3"
    v[:, :, :] = fields["lad"]

    v = nc.createVariable(
        "albedo_pars", "f4", ("nalbedo_pars", "y", "x"), fill_value=-9999.0
    )
    v.long_name = "albedo parameters"
    v.units = "1"
    v[:, :, :] = fields["albedo_pars"]

    v = nc.createVariable(
        "soil_temperature", "f4", ("zsoil", "y", "x"), fill_value=-9999.0
    )
    v.long_name = "initial soil temperature"
    v.units = "K"
    v[:, :, :] = fields["soil_temperature"]

    v = nc.createVariable(
        "soil_moisture", "f4", ("zsoil", "y", "x"), fill_value=-9999.0
    )
    v.long_name = "initial soil moisture"
    v.units = "m3 m-3"
    v[:, :, :] = fields["soil_moisture"]

    v = nc.createVariable(
        "deep_soil_temperature", "f4", ("y", "x"), fill_value=-9999.0
    )
    v.long_name = "deep soil temperature"
    v.units = "K"
    v[:, :] = fields["deep_soil_temperature"]


# --------------------------------------------------------------------------- #
# Command-line interface (batch use)
# --------------------------------------------------------------------------- #
def generate_from_config(config: Dict, zones: Sequence[Dict]) -> str:
    """Read the reference grid named in ``config`` and write the static driver."""
    ref = config.get("reference_file", "")
    if not ref:
        raise ValueError("config['reference_file'] is empty; a reference file is required.")
    grid = read_reference_grid(ref)
    # Reference file is authoritative for the vertical grid; keep nx/ny/dx/dy
    # from the config unless they are still at the template defaults.
    return write_static_driver(config, zones, grid["zu"], grid["zw"])


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    import yaml

    parser = argparse.ArgumentParser(
        description="Generate a PALM static driver from a YAML config and zone list."
    )
    parser.add_argument("config", help="path to config.yaml")
    parser.add_argument(
        "zones",
        nargs="?",
        default=None,
        help="optional YAML/JSON file containing a list of zone dicts",
    )
    parser.add_argument("-o", "--output", default=None, help="output file path")
    args = parser.parse_args(argv)

    with open(args.config, "r") as fh:
        config = yaml.safe_load(fh)

    zones: List[Dict] = []
    if args.zones:
        with open(args.zones, "r") as fh:
            zones = yaml.safe_load(fh) or []

    ref = config.get("reference_file", "")
    if not ref:
        parser.error("config['reference_file'] must point to a PALM 3D output file.")
    grid = read_reference_grid(ref)
    write_static_driver(config, zones, grid["zu"], grid["zw"], args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

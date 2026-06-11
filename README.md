# PALM Static Driver GUI Generator

A desktop (tkinter) tool to paint urban surfaces on a 2-D grid and generate
PALM-compatible **static driver** NetCDF4 files. The output is structurally
identical to the reference `static_driver_soil.py`: same dimensions, variable
names, dtypes, fill values and CF-1.7 attributes.

## Project layout

```
palm_static_generator/
├── __init__.py
├── __main__.py          # `python -m palm_static_generator` launches the GUI
├── README.md
├── core/                # GUI-independent logic (importable for batch use)
│   ├── palm_types.py    #   PALM type-code / name tables (single source of truth)
│   ├── geo.py           #   lat/lon → UTM (WGS84), no GIS dependency
│   └── writer.py        #   NetCDF writer
├── gui/                 # tkinter application
│   ├── app.py           #   main window (entry point)
│   └── canvas_widget.py #   2-D painting canvas (zoom / pan)
├── config/
│   └── config.yaml      #   domain + global-attributes template
└── tests/               # headless smoke tests
    ├── test_writer.py
    └── test_gui_smoke.py
```

`requirements.txt` lives in the project root (one level above this package).

## Installation

The tool needs **Python 3.8+** with `numpy`, `netCDF4`, `PyYAML` and `tkinter`.
It runs on a local Linux workstation and on an HPC login node with X forwarding
(`ssh -X`).

### Option A — the cluster PALM environment (`venv-palm`)

On this cluster all dependencies are already provided by the PALM virtual
environment:

```bash
venv-palm          # alias for: source $PALMWORK_DIR/.venv/bin/activate
```

### Option B — a fresh virtual environment (no `venv-palm`)

Anywhere else, create your own venv and install from `requirements.txt`:

```bash
cd /path/to/palmstaticgenerator        # the project root (contains requirements.txt)
python3 -m venv .venv
source .venv/bin/activate               # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`numpy`, `netCDF4` and `PyYAML` install from PyPI wheels (the netCDF4 wheel
bundles the C library, so no system `libnetcdf` is required).

**tkinter** is part of the standard library but is packaged separately by some
OSes and is *not* installable with pip. Install it through the system package
manager if `python -c "import tkinter"` fails:

| Platform | Command |
|---|---|
| openSUSE / SLES | `sudo zypper install python3-tk` |
| Debian / Ubuntu | `sudo apt install python3-tk` |
| Fedora / RHEL   | `sudo dnf install python3-tkinter` |
| macOS (Homebrew)| `brew install python-tk` |
| conda           | `conda install tk` (tkinter ships with the conda Python) |

Verify the environment:

```bash
python -c "import numpy, netCDF4, yaml, tkinter; print('OK')"
```

`pyproj` is **not** required — the lat/lon → UTM transform is implemented in
`core/geo.py` with the standard library only.

## Running the GUI

From the **project root** (the directory that contains the
`palm_static_generator/` package):

```bash
python -m palm_static_generator
# equivalently: python -m palm_static_generator.gui.app
```

### Workflow

1. **Load a reference NetCDF first.** Click *Load reference NetCDF…* and pick a
   previous PALM 3D output file (`INIT_3d.001.nc` or any `*_3d*.nc`). Its
   vertical grid (`zu_3d`, `zw_3d`) and domain size (`nx, ny, dx, dy`) drive
   everything; the path is stored as the `previous_run` global attribute.
   Until a file is loaded the canvas and controls are disabled.
2. Pick a **drawing tool** (Building / Vegetation / Pavement / Soil) on the
   right, then **click-drag** on the canvas to paint a rectangular zone.
   * **Zoom**: the `−` / `+` / `Fit` buttons above the canvas, or
     **Ctrl + mouse-wheel**. The current zoom level is shown as a percentage.
   * **Pan**: the scrollbars, the **arrow keys** (click the canvas first to
     give it focus), the mouse-wheel (vertical) or **Shift + wheel**
     (horizontal).
3. Select a zone (click it on the canvas or in the list) and edit its
   properties at the bottom right — building height, LAD profile, soil
   temperature profile, type codes, etc. All are editable after drawing.
4. Reorder zones with *Move up/down* — rendering and rasterization use the list
   order, **last on top** (painter's algorithm).
5. **Generate NetCDF** validates the scene and writes the static driver.

The grid is drawn with `j` increasing **upward** (north). Cell bounds are
stored as `numpy` slice bounds `i0:i1, j0:j1` (the upper bound is exclusive).

### Convenience features

- **Domain recap** — the Domain section shows a live summary of the grid:
  `(nx+1) × (ny+1) cells | Lx × Ly m` (physical extent `(nx+1)·dx × (ny+1)·dy`),
  plus how many cells are **painted** by surface zones vs. left as background,
  and the soil-overlay cell count when soil zones exist.
- **UTM auto-fill** — the button *↻ Compute origin_x / origin_y from lat/lon
  (UTM)* fills `origin_x` / `origin_y` from `origin_lat` / `origin_lon` using a
  WGS84 transverse-Mercator (UTM) projection. The UTM zone is derived from the
  longitude and shown next to the button. Implemented in `core/geo.py` with the
  standard Snyder series (no GIS dependency; validated to sub-millimetre against
  `pyproj`). You can still type `origin_x` / `origin_y` manually to override.

## Scripting / batch use (no GUI)

`core/writer.py` is fully importable:

```python
from palm_static_generator.core import writer

grid = writer.read_reference_grid("INIT_3d.001.nc")
zones = [
    {"type": "building", "label": "B1", "i0": 6, "i1": 20, "j0": 20, "j1": 34,
     "building_height": 10.0, "building_type": 2},
    {"type": "vegetation", "label": "Park", "i0": 6, "i1": 26, "j0": 6, "j1": 12,
     "vegetation_type": 4, "soil_type": 2,
     "lad": [0.0, 0.1, 0.35, 0.50, 0.45, 0.25, 0.05]},
]
writer.write_static_driver(config, zones, grid["zu"], grid["zw"], "my_case_static")
```

or from the command line (run from the project root):

```bash
python -m palm_static_generator.core.writer config/config.yaml zones.yaml -o my_case_static
```

where `zones.yaml` is a YAML list of zone dicts (schema documented at the top
of `core/writer.py`).

## Zone dictionary schema

Common keys: `type` (`building`/`vegetation`/`pavement`/`soil`), `label`,
and slice bounds `i0, i1, j0, j1`. Per type:

| type | extra keys |
|---|---|
| building   | `building_height` (m), `building_type` (code) |
| vegetation | `vegetation_type`, `soil_type`, `lad` (7 floats) |
| pavement   | `pavement_type`, `soil_type` |
| soil       | `soil_type`, `soil_temperature` (8 floats), `soil_moisture`, `deep_soil_temperature` |

## Rasterization & validation

The writer reproduces the reference logic: background fill, painter's-algorithm
zone application, per-zone LAD/soil profiles, `surface_fraction` 1-hot per cell
(all-zero on building cells), `buildings_3d[k]` set where
`buildings_2d > zw[k]`, and albedo defaults per surface category. Soil
initial-condition fields are filled over the **entire** domain (matching the
reference; leaving fill values triggers PALM's LSM0046). Before writing it
asserts:

- every cell is in exactly one surface category (counts printed),
- `surface_fraction` sums to 0 on building cells and 1 elsewhere,
- `soil_type` is set wherever `vegetation_type`/`pavement_type` is (DRV0023),
- no cell has both vegetation and pavement,
- every building cell has `building_id > 0` and `buildings_2d > 0`,
- LAD is set on every vegetation-zone cell.

## Notes on parity with `static_driver_soil.py`

- All PALM type codes live **only** in `core/palm_types.py` (PALM 6.0 LSM tables).
- The variable set follows the prompt's specification table. The reference
  script's `zt` (flat terrain = 0) and `water_type` are intentionally **not**
  written: with flat terrain PALM treats absent `zt` as 0, and with no water
  `surface_fraction[2]` is 0 everywhere, so neither is needed.

## Tests

Run from the project root:

```bash
python -m palm_static_generator.tests.test_writer    # headless writer/structure test

# GUI smoke test (needs a display; use Xvfb on a headless node):
Xvfb :99 -screen 0 1280x800x24 &
DISPLAY=:99 python -m palm_static_generator.tests.test_gui_smoke
```

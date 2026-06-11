# PALM Static Driver GUI Generator

A desktop (tkinter) tool to paint urban surfaces on a 2-D grid and generate
PALM-compatible **static driver** NetCDF4 files. The output follows the
reference `static_driver_soil.py` — same dimensions, variable names, dtypes,
fill values and CF-1.7 attributes — extended with `water_type` and terrain
`zt`. Surfaces supported: buildings, vegetation/trees, pavement, water, plus
soil and terrain overlays and a background eraser.

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
└── tests/               # pytest suite (writer + GUI smoke)
    ├── test_writer.py
    └── test_gui_smoke.py
```

`requirements.txt` and `pyproject.toml` live in the project root (one level
above this package).

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

### Optional — install as a package (console command)

From the project root you can install the package so it is importable anywhere
and exposes a `palm-static-gui` command:

```bash
python -m pip install -e .       # editable install (reads pyproject.toml)
palm-static-gui                  # launches the GUI
```

## Running the GUI

From the **project root** (the directory that contains the
`palm_static_generator/` package):

```bash
python -m palm_static_generator
# equivalently: python -m palm_static_generator.gui.app  (or: palm-static-gui if installed)
```

### Workflow

1. **Load a reference NetCDF first.** Click *Load reference NetCDF…* and pick a
   previous PALM 3D output file (`INIT_3d.001.nc` or any `*_3d*.nc`). Its
   vertical grid (`zu_3d`, `zw_3d`) and domain size (`nx, ny, dx, dy`) drive
   everything; the path is stored as the `previous_run` global attribute.
   Until a file is loaded the canvas and controls are disabled.
2. Pick a **drawing tool** on the right, then **click-drag** on the canvas to
   paint a rectangular zone. Tools:
   * **Building / Vegetation / Pavement / Water** — the four mutually exclusive
     surface categories.
   * **Soil** — an overlay setting soil initial conditions (temperature
     profile, moisture, deep temperature) without changing the surface type.
   * **Terrain** — an overlay setting terrain height `zt`.
   * **Erase** — resets cells back to the default background surface.
   * **Select / Move / Reshape** (the *Edit (mouse)* group) — direct
     manipulation of existing zones:
     - **Select** — click a zone to select it, or drag a box (marquee) to
       select every zone it touches. Hold **Shift** or **Ctrl** to add to /
       toggle the current selection. You can also multi-select in the zone list
       (Ctrl/Shift-click).
     - **Move** — drag a zone to translate it; if you grab one of several
       selected zones, **the whole selection moves together**.
     - **Reshape** — drag a zone's edge or corner to resize it.
     All show a live preview, are clamped to the domain, and are undoable; the
     cursor changes to indicate the active mode. With multiple zones selected,
     **Delete** removes them all.
   * **Zoom**: the `−` / `+` / `Fit` buttons above the canvas, or
     **Ctrl + mouse-wheel** (zoom % shown).
   * **Pan**: scrollbars, **arrow keys** (click the canvas first for focus),
     mouse-wheel (vertical) or **Shift + wheel** (horizontal).
   * The canvas shows a colour **legend**, metre **axis ticks**, a **north
     arrow**, and shades buildings darker the taller they are. Hover shows the
     cell `(i, j)` and position in metres.
3. Select a zone (click it on the canvas or in the list) and edit its
   properties at the bottom right — **numeric footprint** (`i0:i1, j0:j1`),
   building height, LAD profile, soil-temperature profile, type codes, etc. All
   are editable after drawing.
4. Reorder zones with *Move up/down* — rendering and rasterization use the list
   order, **last on top** (painter's algorithm).
5. **Undo / redo** (`↶`/`↷` buttons or **Ctrl+Z** / **Ctrl+Y**); **Delete**
   removes the selected zone. **Ctrl+C / Ctrl+X / Ctrl+V** copy / cut / paste the
   selected zone (a paste lands offset by one cell) and **Ctrl+D** duplicates it.
   These keys act on the selected zone when the canvas or zone list has focus
   (so they don't interfere with text copy/paste in the config fields).
6. **Save scene** / **Load scene** persist the config *and* the painted zones
   together (`*.scene.yaml`) so a layout survives between sessions.
7. **Generate NetCDF** validates the scene and writes the static driver.

The grid is drawn with `j` increasing **upward** (north). Cell bounds are
stored as `numpy` slice bounds `i0:i1, j0:j1` (the upper bound is exclusive).

### Convenience features

- **Domain recap** — the Domain section shows a live summary: `(nx+1) × (ny+1)
  cells | Lx × Ly m` (extent `(nx+1)·dx × (ny+1)·dy`), how many cells are
  **painted** vs. background, and soil-overlay / terrain cell counts when those
  zones exist.
- **UTM auto-fill** — the button *↻ Compute origin_x / origin_y from lat/lon
  (UTM)* fills `origin_x` / `origin_y` from `origin_lat` / `origin_lon` using a
  WGS84 transverse-Mercator (UTM) projection. The UTM zone is derived from the
  longitude and shown next to the button. Implemented in `core/geo.py` with the
  standard Snyder series (no GIS dependency; validated to sub-millimetre against
  `pyproj`). You can still type `origin_x` / `origin_y` manually to override.
- **Default soil-temperature profile** — *Edit default soil-temperature
  profile…* opens a dialog to set the 8-layer background profile used for all
  non-soil-override cells.
- **Domain-edit guard** — editing `nx/ny/dx/dy` away from the loaded reference
  grid warns once that coordinate arrays and zone footprints assume the
  reference dimensions.

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

Common keys: `type`, `label`, and slice bounds `i0, i1, j0, j1`. Per type:

| type | extra keys | role |
|---|---|---|
| building   | `building_height` (m), `building_type` (code) | surface |
| vegetation | `vegetation_type`, `soil_type`, `lad` (7 floats) | surface |
| pavement   | `pavement_type`, `soil_type` | surface |
| water      | `water_type` (code) | surface |
| soil       | `soil_type`, `soil_temperature` (8 floats), `soil_moisture`, `deep_soil_temperature` | overlay |
| terrain    | `terrain_height` (m) | overlay |
| background | *(none)* | eraser → resets to default surface |

## Rasterization & validation

The writer reproduces the reference logic: background fill, painter's-algorithm
zone application, per-zone LAD/soil profiles, `surface_fraction` 1-hot per cell
(`[veg, pav, water]`, all-zero on building cells) and albedo defaults per
category. Extensions:

- **`building_id`** is assigned by **4-connected components** of the building
  footprint, so adjacent zones merge into one id and separated ones split
  (pure-numpy/BFS, no scipy dependency).
- **`buildings_3d[k]`** is set where `zt < zw[k] ≤ zt + buildings_2d`; with flat
  terrain (`zt = 0`) this reduces to the reference's `buildings_2d > zw[k]`.
- **terrain** zones write `zt`; **water** zones write `water_type` and
  `surface_fraction[2] = 1`.

Soil initial-condition fields are filled over the **entire** domain (matching
the reference; leaving fill values triggers PALM's LSM0046). `validate_config`
and `validate_zones` run first; then before writing it asserts:

- every cell is in exactly one surface category (building/vegetation/pavement/water),
- `surface_fraction` sums to 0 on building cells and 1 elsewhere,
- `soil_type` is set wherever `vegetation_type`/`pavement_type` is (DRV0023),
- every building cell has `building_id > 0` and `buildings_2d > 0`,
- LAD is set on every vegetation-zone cell.

## Notes on parity with `static_driver_soil.py`

- All PALM type codes live **only** in `core/palm_types.py` (PALM 6.0 LSM tables).
- Dimensions, dtypes, fill values and CF-1.7 attributes match the reference.
- This generator additionally writes `water_type` and terrain `zt` (the
  reference omitted them as it had no water and flat terrain). They are inert
  for a flat, water-free scene: `zt = 0` everywhere and `water_type` is fill.

## Tests

The suite uses **pytest**. The GUI tests skip automatically when no `$DISPLAY`
is set, so the core tests run anywhere. From the project root:

```bash
pytest                              # all tests (GUI tests skip if headless)

# include the GUI tests on a headless node via a virtual framebuffer:
Xvfb :99 -screen 0 1280x800x24 &
DISPLAY=:99 pytest
```

Individual files are also runnable directly, e.g.
`python -m palm_static_generator.tests.test_writer`.

"""PALM land-surface-model lookup tables and shared constants.

All PALM type *codes* used anywhere in this tool are defined here and only
here.  No other module may hard-code a numeric type code: it must import the
relevant dictionary or constant from this module instead.

The tables follow the PALM 6.0 land-surface-model classification.  Each table
maps an integer code to a short human-readable name.  Helper functions build
the "code: name" strings used to populate the GUI drop-downs and parse the
selected string back to an integer code.
"""

from typing import Dict, List

# --------------------------------------------------------------------------- #
# Surface-classification tables (code -> name)
# --------------------------------------------------------------------------- #

#: Vegetation classification (PALM ``vegetation_type``).
VEGETATION_TYPES: Dict[int, str] = {
    1: "bare soil",
    2: "crops",
    3: "short grass",
    4: "broadleaved trees",
    5: "needleleaved trees",
    6: "mixed forest",
    7: "orchard",
    8: "shrubs",
}

#: Pavement classification (PALM ``pavement_type``).
PAVEMENT_TYPES: Dict[int, str] = {
    1: "asphalt road",
    2: "concrete",
    3: "cobblestone",
    4: "metal",
    5: "wood",
}

#: Soil classification (PALM ``soil_type``).
SOIL_TYPES: Dict[int, str] = {
    1: "coarse (sand)",
    2: "medium (loamy sand)",
    3: "medium-fine",
    4: "fine (clay)",
    5: "very fine",
    6: "organic",
}

#: Building classification (PALM ``building_type``).
BUILDING_TYPES: Dict[int, str] = {
    1: "residential low-rise",
    2: "residential high-rise",
    3: "commercial",
    4: "industrial",
    5: "road",
    6: "bridge",
    7: "undefined",
}

#: Water classification (PALM ``water_type``).
WATER_TYPES: Dict[int, str] = {
    1: "lake",
    2: "river",
    3: "ocean",
    4: "pond",
    5: "fountain",
}

# --------------------------------------------------------------------------- #
# Numeric / profile constants
# --------------------------------------------------------------------------- #

#: ``vegetation_type`` code that represents unresolved bare soil.
BARE_SOIL_VEGETATION_TYPE: int = 1

#: Default broadleaved-tree leaf-area-density profile for the 7 ``zlad`` levels.
DEFAULT_LAD_PROFILE: List[float] = [0.0, 0.1, 0.35, 0.50, 0.45, 0.25, 0.05]

#: Number of canopy (``zlad``) levels.
N_ZLAD: int = 7

#: PALM default soil-layer thicknesses [m] (the 8-layer soil grid).
ZSOIL_DZ: List[float] = [0.01, 0.02, 0.04, 0.06, 0.14, 0.26, 0.54, 1.86]

#: Number of soil layers (length of :data:`ZSOIL_DZ`).
N_ZSOIL: int = len(ZSOIL_DZ)

#: Number of surface-fraction categories: 0 vegetation, 1 pavement, 2 water.
N_SURFACE_FRACTION: int = 3

#: Number of albedo parameters.
N_ALBEDO_PARS: int = 7

# Default albedo parameter vectors per surface category.  The 7 entries are,
# in order: shortwave-direct, shortwave-diffuse, longwave-emissivity,
# visible-direct, visible-diffuse, near-infrared-direct, near-infrared-diffuse.
# Values reproduce those used in the reference ``static_driver_soil.py``.
ALBEDO_DEFAULTS: Dict[str, List[float]] = {
    "pavement": [0.18, 0.18, 0.92, 0.14, 0.14, 0.22, 0.22],
    "building": [0.25, 0.25, 0.90, 0.20, 0.20, 0.30, 0.30],
    "vegetation": [0.20, 0.20, 0.97, 0.10, 0.10, 0.30, 0.30],
    "water": [0.08, 0.08, 0.98, 0.06, 0.06, 0.10, 0.10],
}

# --------------------------------------------------------------------------- #
# Drop-down helpers
# --------------------------------------------------------------------------- #


def options(table: Dict[int, str]) -> List[str]:
    """Return ``["code: name", ...]`` strings for a type table, sorted by code.

    Used to populate ttk ``Combobox`` widgets.
    """
    return ["{}: {}".format(code, table[code]) for code in sorted(table)]


def parse_code(option: str) -> int:
    """Parse the integer code out of a ``"code: name"`` drop-down string."""
    return int(str(option).split(":", 1)[0].strip())


def option_for(table: Dict[int, str], code: int) -> str:
    """Return the ``"code: name"`` string for ``code`` in ``table``.

    Falls back to ``"<code>: ?"`` if the code is not present in the table.
    """
    name = table.get(int(code))
    if name is None:
        return "{}: ?".format(code)
    return "{}: {}".format(code, name)

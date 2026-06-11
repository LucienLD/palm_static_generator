"""Geographic helpers — latitude/longitude to UTM (WGS84).

Implemented with the standard Snyder transverse-Mercator series (accurate to
the millimetre over a UTM zone), using only the Python standard library so the
tool keeps its "no GIS libraries" dependency footprint (numpy / netCDF4 /
PyYAML / tkinter).  This lets the GUI fill ``origin_x`` / ``origin_y``
automatically from ``origin_lat`` / ``origin_lon``.
"""

import math
from typing import Dict

# WGS84 ellipsoid
_A = 6378137.0                      # semi-major axis [m]
_F = 1.0 / 298.257223563            # flattening
_E2 = _F * (2.0 - _F)               # first eccentricity squared
_EP2 = _E2 / (1.0 - _E2)            # second eccentricity squared
_K0 = 0.9996                        # UTM scale factor
_FALSE_EASTING = 500000.0
_FALSE_NORTHING_SOUTH = 10000000.0  # added in the southern hemisphere


def utm_zone(lon_deg: float) -> int:
    """Return the UTM zone number (1..60) for a longitude in degrees."""
    return int(math.floor((lon_deg + 180.0) / 6.0) % 60) + 1


def _central_meridian_deg(zone: int) -> float:
    """Central-meridian longitude [deg] of a UTM zone."""
    return (zone - 1) * 6.0 - 180.0 + 3.0


def latlon_to_utm(lat_deg: float, lon_deg: float) -> Dict[str, object]:
    """Convert WGS84 latitude/longitude (degrees) to UTM easting/northing.

    Returns a dict with ``easting``, ``northing`` (metres), ``zone`` (number),
    and ``hemisphere`` (``"N"`` or ``"S"``).  The UTM zone is derived from the
    longitude.
    """
    zone = utm_zone(lon_deg)
    lon0 = math.radians(_central_meridian_deg(zone))
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)

    n = _A / math.sqrt(1.0 - _E2 * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = _EP2 * cos_lat * cos_lat
    a = (lon - lon0) * cos_lat

    # meridional arc
    m = _A * (
        (1.0 - _E2 / 4.0 - 3.0 * _E2 ** 2 / 64.0 - 5.0 * _E2 ** 3 / 256.0) * lat
        - (3.0 * _E2 / 8.0 + 3.0 * _E2 ** 2 / 32.0 + 45.0 * _E2 ** 3 / 1024.0)
        * math.sin(2.0 * lat)
        + (15.0 * _E2 ** 2 / 256.0 + 45.0 * _E2 ** 3 / 1024.0) * math.sin(4.0 * lat)
        - (35.0 * _E2 ** 3 / 3072.0) * math.sin(6.0 * lat)
    )

    easting = (
        _K0 * n * (
            a
            + (1.0 - t + c) * a ** 3 / 6.0
            + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * _EP2) * a ** 5 / 120.0
        )
        + _FALSE_EASTING
    )

    northing = _K0 * (
        m
        + n * tan_lat * (
            a * a / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * a ** 4 / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * _EP2) * a ** 6 / 720.0
        )
    )

    hemisphere = "N" if lat_deg >= 0.0 else "S"
    if hemisphere == "S":
        northing += _FALSE_NORTHING_SOUTH

    return {
        "easting": easting,
        "northing": northing,
        "zone": zone,
        "hemisphere": hemisphere,
    }

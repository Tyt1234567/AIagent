"""
Real-world scalar field data: multiple user-supplied or online-fetched
layers over an actual geographic bounding box, as an alternative to the
synthetic town in data.py. Feeds the same generic Morse engine
(geoai_agent/morse_topology.py) the synthetic fields do.

Two ways to get a layer's data onto the analysis grid:
- User-supplied CSV of scattered (x, y, value) samples, interpolated onto
  the grid with inverse-distance weighting (pure numpy, no scipy).
- Online fetch, live at request time (nothing is ever pre-downloaded or
  cached to disk): any of several free, keyless Open-Meteo endpoints --
  terrain elevation, or a live current-conditions weather variable
  (temperature, precipitation, wind speed, humidity, surface pressure).
  This is deliberately not elevation-only: OPEN_METEO_VARIABLES is a small
  registry so the same fetch/analysis path works for whichever topic a
  query is actually about (see infer_variable_from_text).

A "slope" layer (gradient magnitude of elevation) is derived for free from
whatever elevation data is loaded, giving a second genuinely real layer
even when the user supplies nothing else -- a concrete demonstration of
the multi-layer path.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.request
import urllib.parse

import numpy as np

from geoai_agent.morse_topology import ScalarField

GRID_RESOLUTION = 20
_FETCH_BATCH = 100  # conservative shared batch size, verified against the elevation endpoint's limit

# Every entry is a live, keyless Open-Meteo endpoint. "elevation" hits the
# dedicated elevation API; everything else hits the forecast API's
# `current=<variable>` field, which returns live current-conditions data
# (not historical/cached) for each requested coordinate.
OPEN_METEO_VARIABLES: dict[str, dict] = {
    "elevation": {"keywords": ["elevation", "terrain", "dem", "lidar", "height", "altitude", "topograph"]},
    "temperature_2m": {"keywords": ["temperature", "heat", "warm", "cold", "thermal"]},
    "precipitation": {"keywords": ["precipitation", "rain", "rainfall", "flood", "storm", "wet"]},
    "wind_speed_10m": {"keywords": ["wind"]},
    "relative_humidity_2m": {"keywords": ["humidity", "moisture"]},
    "surface_pressure": {"keywords": ["pressure", "barometric"]},
}


def infer_variable_from_text(text: str, default: str = "elevation") -> str:
    """Keyword-based topic classification: which OPEN_METEO_VARIABLES entry
    a free-text query is asking about. Deliberately not elevation-only --
    this is what lets the real-world pipeline handle "any topic" rather
    than assuming terrain every time. Falls back to `default` if nothing
    matches."""
    lower = text.lower()
    for variable, info in OPEN_METEO_VARIABLES.items():
        if any(kw in lower for kw in info["keywords"]):
            return variable
    return default


_COORD_RE = re.compile(
    # No leading "-?" on the number: sign comes entirely from the N/S/E/W
    # suffix below. A leading minus would misparse "40.96N-41.15N" (a
    # hyphen-separated range with no spaces) as if the second number were
    # negative, since the range-separating hyphen sits directly against it.
    r"(\d+(?:\.\d+)?)\s*"
    r"(?:\^\s*\\?circ)?\s*"           # optional ^\circ / ^circ (LaTeX degree)
    r"(?:[°˚]|\bdegrees?\b)?\s*"  # optional degree symbol/word
    r"(?:\\?text\s*\{)?\s*"           # optional \text{ wrapper
    r"([NSEWnsew])\}?"
)


def parse_bounds_from_text(text: str) -> tuple[float, float, float, float] | None:
    """Extracts an explicit lat/lon bounding box from free text, e.g.
    "40.96 N - 41.15 N, 75.15 W - 74.95 W" (also tolerates LaTeX-ish
    markup like degree symbols / \\text{} around the number). Needs at
    least two latitude (N/S) and two longitude (E/W) matches; returns None
    if it can't find an unambiguous box (the caller should then fall back
    to another route rather than guess at coordinates)."""
    lats, lons = [], []
    for value, direction in _COORD_RE.findall(text):
        v = float(value)
        d = direction.upper()
        if d == "S":
            v = -v
        elif d == "W":
            v = -v
        (lats if d in "NS" else lons).append(v)

    if len(lats) < 2 or len(lons) < 2:
        return None
    return (min(lats), max(lats), min(lons), max(lons))


class RealWorldDataset:
    """Geographic bounding-box dataset made of one or more named
    ScalarField layers. Distinct from the synthetic GeoDataset: it carries
    no road network/zones, only scalar fields for the morse_needed branch."""

    def __init__(self, name: str, bounds: tuple[float, float, float, float], scalar_fields: dict[str, ScalarField]):
        # bounds = (lat_min, lat_max, lon_min, lon_max)
        self.name = name
        self.bounds = bounds
        self.is_geographic = True
        self.scalar_fields = scalar_fields


def _grid(bounds: tuple[float, float, float, float], resolution: int) -> tuple[np.ndarray, np.ndarray]:
    lat_min, lat_max, lon_min, lon_max = bounds
    xs = np.linspace(lon_min, lon_max, resolution)  # x = longitude
    ys = np.linspace(lat_min, lat_max, resolution)  # y = latitude
    return xs, ys


def idw_interpolate(
    px: np.ndarray, py: np.ndarray, pv: np.ndarray,
    xs: np.ndarray, ys: np.ndarray, power: float = 2.0,
) -> np.ndarray:
    """Inverse-distance-weighted interpolation of scattered points (px, py, pv)
    onto a regular (len(ys), len(xs)) grid. A point that lands exactly on a
    grid node is returned exactly (avoids a division by zero)."""
    xx, yy = np.meshgrid(xs, ys)  # (ny, nx)
    values = np.zeros_like(xx)
    weight_sum = np.zeros_like(xx)
    for x0, y0, v0 in zip(px, py, pv):
        dist2 = (xx - x0) ** 2 + (yy - y0) ** 2
        on_point = dist2 < 1e-12
        if np.any(on_point):
            values[on_point] = v0
            weight_sum[on_point] = 1.0
            dist2 = np.where(on_point, np.inf, dist2)
        weight = 1.0 / (dist2 ** (power / 2))
        values += np.where(on_point, 0.0, weight * v0)
        weight_sum += np.where(on_point, 0.0, weight)
    return values / weight_sum


def load_csv_points(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reads a CSV with header columns x,y,value (x=longitude, y=latitude
    for real-world layers)."""
    xs, ys, vs = [], [], []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            xs.append(float(row["x"]))
            ys.append(float(row["y"]))
            vs.append(float(row["value"]))
    if not xs:
        raise ValueError(f"no data rows found in {path}")
    return np.array(xs), np.array(ys), np.array(vs)


def scalar_field_from_csv(
    name: str, path: str, bounds: tuple[float, float, float, float],
    resolution: int = GRID_RESOLUTION, power: float = 2.0,
) -> ScalarField:
    """Builds a ScalarField for `name` by IDW-interpolating user-supplied
    scattered samples in `path` onto the analysis grid over `bounds`."""
    px, py, pv = load_csv_points(path)
    xs, ys = _grid(bounds, resolution)
    values = idw_interpolate(px, py, pv, xs, ys, power=power)
    return ScalarField(name=name, xs=xs, ys=ys, values=values)


def fetch_open_meteo_field(
    variable: str, bounds: tuple[float, float, float, float], resolution: int = GRID_RESOLUTION,
) -> ScalarField:
    """Live-fetches `variable` (a key in OPEN_METEO_VARIABLES) for every
    grid node over `bounds` from Open-Meteo, batched at a conservative
    coordinate-pair-per-request limit. Always a fresh HTTP request at call
    time -- nothing is downloaded ahead of time or cached to disk."""
    if variable not in OPEN_METEO_VARIABLES:
        raise ValueError(f"unknown Open-Meteo variable '{variable}', available: {list(OPEN_METEO_VARIABLES)}")

    xs, ys = _grid(bounds, resolution)
    xx, yy = np.meshgrid(xs, ys)
    lons = xx.ravel()
    lats = yy.ravel()
    out = np.empty(lons.shape[0], dtype=float)

    for start in range(0, lons.shape[0], _FETCH_BATCH):
        end = start + _FETCH_BATCH
        lat_param = ",".join(f"{v:.6f}" for v in lats[start:end])
        lon_param = ",".join(f"{v:.6f}" for v in lons[start:end])

        if variable == "elevation":
            query = urllib.parse.urlencode({"latitude": lat_param, "longitude": lon_param})
            with urllib.request.urlopen(f"https://api.open-meteo.com/v1/elevation?{query}", timeout=30) as resp:
                payload = json.loads(resp.read())
            out[start:end] = payload["elevation"]
        else:
            query = urllib.parse.urlencode({"latitude": lat_param, "longitude": lon_param, "current": variable})
            with urllib.request.urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=30) as resp:
                payload = json.loads(resp.read())
            out[start:end] = [entry["current"][variable] for entry in payload]

    values = out.reshape(xx.shape)
    return ScalarField(name=variable, xs=xs, ys=ys, values=values)


def fetch_elevation_field(
    bounds: tuple[float, float, float, float], resolution: int = GRID_RESOLUTION,
) -> ScalarField:
    """Fetches real terrain elevation -- thin wrapper over
    fetch_open_meteo_field kept for existing callers (real_world_example.py)."""
    return fetch_open_meteo_field("elevation", bounds, resolution)


def derive_slope_field(elevation: ScalarField) -> ScalarField:
    """Gradient magnitude of an elevation field -- a second, genuinely
    real layer derived at no extra data-fetch cost, useful for spotting
    steep terrain (ridgelines/cliffs) rather than just highs and lows."""
    dy, dx = np.gradient(elevation.values, elevation.ys, elevation.xs)
    slope = np.sqrt(dx ** 2 + dy ** 2)
    return ScalarField(name="slope", xs=elevation.xs, ys=elevation.ys, values=slope)


def build_real_world_dataset(
    name: str,
    bounds: tuple[float, float, float, float],
    layer_sources: dict[str, str | None] | None = None,
    resolution: int = GRID_RESOLUTION,
    include_slope: bool = True,
) -> RealWorldDataset:
    """Builds a RealWorldDataset with one or more named layers.

    layer_sources maps layer name -> CSV path, or -> None to mean "fetch
    online" (only supported for the "elevation" layer, via Open-Meteo). If
    omitted entirely, defaults to fetching elevation online.
    """
    layer_sources = layer_sources or {"elevation": None}
    fields: dict[str, ScalarField] = {}

    for layer_name, source in layer_sources.items():
        if source is not None:
            fields[layer_name] = scalar_field_from_csv(layer_name, source, bounds, resolution)
        elif layer_name in OPEN_METEO_VARIABLES:
            fields[layer_name] = fetch_open_meteo_field(layer_name, bounds, resolution)
        else:
            raise ValueError(
                f"layer '{layer_name}' has no CSV source and is not a live-fetchable "
                f"variable (available: {list(OPEN_METEO_VARIABLES)})"
            )

    if include_slope and "elevation" in fields and "slope" not in fields:
        fields["slope"] = derive_slope_field(fields["elevation"])

    return RealWorldDataset(name=name, bounds=bounds, scalar_fields=fields)

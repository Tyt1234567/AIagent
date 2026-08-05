"""
Real-world scalar field data: multiple user-supplied or online-fetched
layers over an actual geographic bounding box, as an alternative to the
synthetic town in data.py. Feeds the same generic Morse engine
(geoai_agent/morse_topology.py) the synthetic fields do.

Two ways to get a layer's data onto the analysis grid:
- User-supplied CSV of scattered (x, y, value) samples, interpolated onto
  the grid with inverse-distance weighting (pure numpy, no scipy).
- Online fetch: real elevation from the Open-Meteo elevation API
  (https://open-meteo.com/en/docs/elevation-api), free and keyless. The API
  caps each request at 100 coordinate pairs, so requests are batched.

A "slope" layer (gradient magnitude of elevation) is derived for free from
whatever elevation data is loaded, giving a second genuinely real layer
even when the user supplies nothing else -- a concrete demonstration of
the multi-layer path.
"""

from __future__ import annotations

import csv
import json
import urllib.request
import urllib.parse

import numpy as np

from geoai_agent.morse_topology import ScalarField

GRID_RESOLUTION = 20
_ELEVATION_BATCH = 100
_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"


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


def fetch_elevation_field(
    bounds: tuple[float, float, float, float], resolution: int = GRID_RESOLUTION,
) -> ScalarField:
    """Fetches real terrain elevation for every grid node over `bounds` from
    the free, keyless Open-Meteo elevation API, batched at the API's ~100
    coordinate-pair-per-request limit."""
    xs, ys = _grid(bounds, resolution)
    xx, yy = np.meshgrid(xs, ys)
    lons = xx.ravel()
    lats = yy.ravel()

    elevations = np.empty(lons.shape[0], dtype=float)
    for start in range(0, lons.shape[0], _ELEVATION_BATCH):
        end = start + _ELEVATION_BATCH
        lat_param = ",".join(f"{v:.6f}" for v in lats[start:end])
        lon_param = ",".join(f"{v:.6f}" for v in lons[start:end])
        query = urllib.parse.urlencode({"latitude": lat_param, "longitude": lon_param})
        with urllib.request.urlopen(f"{_ELEVATION_URL}?{query}", timeout=30) as resp:
            payload = json.loads(resp.read())
        elevations[start:end] = payload["elevation"]

    values = elevations.reshape(xx.shape)
    return ScalarField(name="elevation", xs=xs, ys=ys, values=values)


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
        elif layer_name == "elevation":
            fields["elevation"] = fetch_elevation_field(bounds, resolution)
        else:
            raise ValueError(
                f"layer '{layer_name}' has no CSV source and online fetch is only "
                "available for 'elevation'"
            )

    if include_slope and "elevation" in fields and "slope" not in fields:
        fields["slope"] = derive_slope_field(fields["elevation"])

    return RealWorldDataset(name=name, bounds=bounds, scalar_fields=fields)

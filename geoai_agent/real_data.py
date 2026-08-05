"""
Real-world scalar field data: multiple user-supplied or online-fetched
layers over an actual geographic bounding box, as an alternative to the
synthetic town in data.py. Feeds the same generic Morse engine
(geoai_agent/morse_topology.py) the synthetic fields do.

Two ways to get a layer's data onto the analysis grid:
- User-supplied scattered (x, y, value) samples -- a CSV with an x,y,value
  header, or an ESRI shapefile (POINT geometry + a "value" attribute
  field, as a .zip bundling .shp/.shx/.dbf) -- interpolated onto the grid
  with inverse-distance weighting (pure numpy, no scipy; shapefile
  parsing uses pyshp, a small pure-Python library with no further
  dependencies). A layer can also carry a free-text description of what
  it measures, passed through to the LLM recommendation step.
- Online fetch, live at request time (nothing is ever pre-downloaded or
  cached to disk): any of several free, keyless Open-Meteo endpoints --
  terrain elevation, or a live current-conditions weather variable
  (temperature, precipitation, wind speed, humidity, surface pressure).
  This is deliberately not elevation-only: OPEN_METEO_VARIABLES is a small
  registry so the same fetch/analysis path works for whichever topic a
  query is actually about (see prompts.REALWORLD_QUERY_EXTRACTION_SYSTEM
  for how a query is mapped onto one of these).

A "slope" layer (gradient magnitude of elevation) is derived for free from
whatever elevation data is loaded, giving a second genuinely real layer
even when the user supplies nothing else -- a concrete demonstration of
the multi-layer path.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.error
import urllib.request
import urllib.parse

import numpy as np

from geoai_agent.morse_topology import ScalarField

GRID_RESOLUTION = 20
_FETCH_BATCH = 100  # conservative shared batch size, verified against the elevation endpoint's limit

# Every entry is a live, keyless Open-Meteo endpoint. "elevation" hits the
# dedicated elevation API; everything else hits the forecast API's
# `current=<variable>` field, which returns live current-conditions data
# (not historical/cached) for each requested coordinate. Which one a given
# free-text query is about, and what place/coordinates it names, is
# figured out by an LLM extraction call (see
# prompts.REALWORLD_QUERY_EXTRACTION_SYSTEM) rather than regex/keyword
# matching here -- natural-language phrasing varies too much (prepositions,
# capitalization, trailing qualifier clauses) for a hand-written pattern to
# keep up with reliably.
OPEN_METEO_VARIABLES = [
    "elevation", "temperature_2m", "precipitation",
    "wind_speed_10m", "relative_humidity_2m", "surface_pressure",
]


# -- geocoding: turn a place name into a bounding box ---------------------
#
# So a query naming a real place ("College Park, MD") works without the
# user having to look up and type coordinates themselves. Live, keyless,
# same Open-Meteo family as the scalar-field fetches above -- nothing is
# pre-downloaded or cached.

_US_STATE_ABBREVIATIONS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
_TRAILING_STATE_RE = re.compile(r",\s*([A-Za-z]{2})\s*$")


def _expand_trailing_state_abbreviation(place_name: str) -> str:
    """Open-Meteo's geocoding search matches full state names, not
    2-letter abbreviations -- "College Park, MD" returns nothing, but
    "College Park, Maryland" does. Expands a trailing ", XX" if XX is a
    known US state/DC abbreviation; leaves anything else untouched."""
    m = _TRAILING_STATE_RE.search(place_name)
    if not m:
        return place_name
    full = _US_STATE_ABBREVIATIONS.get(m.group(1).upper())
    if not full:
        return place_name
    return place_name[: m.start()] + ", " + full


def geocode_place(place_name: str) -> dict | None:
    """Live lookup of a place name via Open-Meteo's free geocoding API.
    Returns the best-matching {"name", "admin1", "country", "latitude",
    "longitude"} or None if nothing matched."""
    for candidate in (place_name, _expand_trailing_state_abbreviation(place_name)):
        query = urllib.parse.urlencode({"name": candidate, "count": 1, "format": "json"})
        with urllib.request.urlopen(
            f"https://geocoding-api.open-meteo.com/v1/search?{query}", timeout=15
        ) as resp:
            payload = json.loads(resp.read())
        results = payload.get("results") or []
        if results:
            r = results[0]
            return {
                "name": r["name"], "admin1": r.get("admin1"), "country": r.get("country"),
                "latitude": r["latitude"], "longitude": r["longitude"],
            }
    return None


def bounding_box_around_point(
    latitude: float, longitude: float, half_width_deg: float = 0.06
) -> tuple[float, float, float, float]:
    """A small lat/lon bounding box centered on a geocoded point (roughly
    town/neighborhood scale at typical mid-latitudes -- default
    half_width_deg=0.06 is about 13km tall)."""
    return (latitude - half_width_deg, latitude + half_width_deg,
            longitude - half_width_deg, longitude + half_width_deg)


class RealWorldDataset:
    """Geographic bounding-box dataset made of one or more named
    ScalarField layers. Distinct from the synthetic GeoDataset: it carries
    no road network/zones, only scalar fields for the morse_needed branch."""

    def __init__(
        self, name: str, bounds: tuple[float, float, float, float], scalar_fields: dict[str, ScalarField],
        layer_descriptions: dict[str, str] | None = None,
        layer_sources: dict[str, str | None] | None = None,
    ):
        # bounds = (lat_min, lat_max, lon_min, lon_max)
        self.name = name
        self.bounds = bounds
        self.is_geographic = True
        self.scalar_fields = scalar_fields
        # free-text, user-supplied explanation of what a custom (non-Open-Meteo)
        # layer actually measures -- passed to the LLM recommendation so it
        # can interpret a field it has no built-in knowledge of.
        self.layer_descriptions = layer_descriptions or {}
        # the layer_sources this was built from (None = live-fetched,
        # a path = user-uploaded) -- kept around so a caller that reuses a
        # cached dataset can still correctly report where the data came from.
        self.layer_sources = layer_sources or {}


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


def load_shapefile_points(path: str, value_field: str = "value") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reads point samples from an ESRI shapefile (a .zip bundling
    .shp/.shx/.dbf, or a bare .shp alongside its sidecar files) via pyshp.
    Requires a POINT shape type and an attribute field named `value`
    (case-insensitive) holding the scalar sample value."""
    import shapefile

    reader = shapefile.Reader(path)
    if reader.shapeType not in (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM):
        raise ValueError(
            f"shapefile '{path}' has shape type {reader.shapeType!r}, expected POINT "
            "(scattered value samples, same role as the CSV x,y,value format)"
        )
    field_names = [f[0] for f in reader.fields[1:]]  # skip the DeletionFlag pseudo-field
    matches = [f for f in field_names if f.lower() == value_field.lower()]
    if not matches:
        raise ValueError(
            f"shapefile '{path}' has no attribute field named '{value_field}' "
            f"(found: {field_names}); add one holding the scalar sample value"
        )
    field = matches[0]

    xs, ys, vs = [], [], []
    for shape_record in reader.shapeRecords():
        x, y = shape_record.shape.points[0]
        xs.append(x)
        ys.append(y)
        vs.append(float(shape_record.record[field]))
    if not xs:
        raise ValueError(f"no point features found in shapefile '{path}'")
    return np.array(xs), np.array(ys), np.array(vs)


def load_layer_points(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Dispatches to the CSV or shapefile loader based on file extension
    (.zip/.shp -> shapefile, everything else -> CSV)."""
    lower = path.lower()
    if lower.endswith(".zip") or lower.endswith(".shp"):
        return load_shapefile_points(path)
    return load_csv_points(path)


def scalar_field_from_upload(
    name: str, path: str, bounds: tuple[float, float, float, float],
    resolution: int = GRID_RESOLUTION, power: float = 2.0,
) -> ScalarField:
    """Builds a ScalarField for `name` by IDW-interpolating user-supplied
    scattered samples (CSV or shapefile, see load_layer_points) onto the
    analysis grid over `bounds`."""
    px, py, pv = load_layer_points(path)
    xs, ys = _grid(bounds, resolution)
    values = idw_interpolate(px, py, pv, xs, ys, power=power)
    return ScalarField(name=name, xs=xs, ys=ys, values=values)


# kept for existing callers -- CSV was the only supported upload format
# before shapefile support was added.
scalar_field_from_csv = scalar_field_from_upload


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

    num_batches = -(-lons.shape[0] // _FETCH_BATCH)  # ceil division
    for batch_idx, start in enumerate(range(0, lons.shape[0], _FETCH_BATCH)):
        end = start + _FETCH_BATCH
        lat_param = ",".join(f"{v:.6f}" for v in lats[start:end])
        lon_param = ",".join(f"{v:.6f}" for v in lons[start:end])

        if variable == "elevation":
            url = f"https://api.open-meteo.com/v1/elevation?{urllib.parse.urlencode({'latitude': lat_param, 'longitude': lon_param})}"
        else:
            url = (
                "https://api.open-meteo.com/v1/forecast?"
                f"{urllib.parse.urlencode({'latitude': lat_param, 'longitude': lon_param, 'current': variable})}"
            )
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            reason = exc.read().decode("utf-8", errors="replace")
            try:
                reason = json.loads(reason).get("reason", reason)
            except json.JSONDecodeError:
                pass
            raise RuntimeError(
                f"Open-Meteo request failed on batch {batch_idx + 1}/{num_batches} "
                f"({exc.code} {exc.reason}): {reason}. Try a lower resolution or a smaller "
                "bounding box, or wait a moment before retrying."
            ) from exc

        if variable == "elevation":
            out[start:end] = payload["elevation"]
        else:
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
    layer_descriptions: dict[str, str] | None = None,
) -> RealWorldDataset:
    """Builds a RealWorldDataset with one or more named layers.

    layer_sources maps layer name -> a CSV or shapefile (.zip/.shp) path,
    or -> None to mean "fetch online" (only supported for
    OPEN_METEO_VARIABLES entries). If omitted entirely, defaults to
    fetching elevation online.

    layer_descriptions optionally maps layer name -> a free-text
    explanation of what it measures, carried through on the returned
    dataset for the caller to pass to an LLM recommendation step (custom
    uploaded layers have no built-in meaning the way "elevation" does).
    """
    layer_sources = layer_sources or {"elevation": None}
    fields: dict[str, ScalarField] = {}

    for layer_name, source in layer_sources.items():
        if source is not None:
            fields[layer_name] = scalar_field_from_upload(layer_name, source, bounds, resolution)
        elif layer_name in OPEN_METEO_VARIABLES:
            fields[layer_name] = fetch_open_meteo_field(layer_name, bounds, resolution)
        else:
            raise ValueError(
                f"layer '{layer_name}' has no uploaded source and is not a live-fetchable "
                f"variable (available: {list(OPEN_METEO_VARIABLES)})"
            )

    if include_slope and "elevation" in fields and "slope" not in fields:
        fields["slope"] = derive_slope_field(fields["elevation"])

    return RealWorldDataset(
        name=name, bounds=bounds, scalar_fields=fields, layer_descriptions=layer_descriptions,
        layer_sources=layer_sources,
    )

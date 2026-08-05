"""
Web UI for the GeoAI Agent: Flask backend + JS/Leaflet frontend.

Two panels, each backed by JSON API endpoints:
- Town Scenario: free-text natural-language queries against the synthetic
  town (geoai_agent.agent.GeoAIAgent / GeoDataset), with a human-feedback
  re-run loop and a map of the town's road network, zones, hazard zone,
  and population points -- overlaid with a scalar-field heatmap and
  critical points when the query routes to Morse analysis.
- Real-World Data: a lat/lon bounding box and topic (elevation, temperature,
  precipitation, wind, humidity, pressure -- live-fetched, never
  pre-downloaded), fillable either by hand or from a free-text query via
  Smart Query's LLM-based understanding step, plus any number of
  user-uploaded CSV/shapefile layers, all over a real OpenStreetMap
  basemap. One bounds/topic state, one "Fetch & Analyze" action --
  Smart Query only ever fills in the fields this endpoint reads.

This folder is self-contained: geoai_agent/ below is a standalone copy of
the analysis package (not an import of anything outside web_app/), and
examples/ holds its own sample data. Run with:
    python app.py
from inside web_app/, or:
    python web_app/app.py
from the project root -- both work, since Python always puts the launched
script's own directory on sys.path first.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

from flask import Flask, jsonify, render_template, request
from shapely.geometry import mapping

from geoai_agent import prompts
from geoai_agent.agent import GeoAIAgent
from geoai_agent.data import DOMAIN_BOUNDS, GeoDataset
from geoai_agent.llm_client import LLMClient
from geoai_agent.morse_topology import analyze_scalar_field
from geoai_agent.real_data import (
    OPEN_METEO_VARIABLES,
    bounding_box_around_point,
    build_real_world_dataset,
    geocode_place,
)
from geoai_agent.visualize import critical_points_to_geojson, field_to_geojson

app = Flask(__name__)

_agent: GeoAIAgent | None = None


def get_agent() -> GeoAIAgent:
    """Lazily builds one shared agent/dataset for the process -- the
    synthetic town is static, so there is no need to rebuild it per request."""
    global _agent
    if _agent is None:
        _agent = GeoAIAgent(llm_client=LLMClient(), dataset=GeoDataset())
    return _agent


@app.route("/")
def index():
    return render_template("index.html")


# -- Town Scenario (synthetic dataset, full LLM-routed pipeline) --------

@app.route("/api/town/base_map")
def town_base_map():
    dataset = get_agent().dataset

    roads = [
        {
            "type": "Feature",
            "properties": {
                "u": u, "v": v,
                "is_bridge_structure": data.get("is_bridge_structure", False),
            },
            "geometry": mapping(data["geom"]),
        }
        for u, v, data in dataset.road_graph.edges(data=True)
    ]
    zones = [
        {"type": "Feature", "properties": {"name": name}, "geometry": mapping(poly)}
        for name, poly in dataset.zones.items()
    ]
    population = [
        {
            "type": "Feature",
            "properties": {"population": p.population, "nearest_node": p.nearest_node},
            "geometry": mapping(p.point),
        }
        for p in dataset.population_points
    ]

    xmin, ymin, xmax, ymax = DOMAIN_BOUNDS
    return jsonify({
        "bounds": [[ymin, xmin], [ymax, xmax]],  # Leaflet [[y,x],[y,x]]
        "roads": {"type": "FeatureCollection", "features": roads},
        "zones": {"type": "FeatureCollection", "features": zones},
        "hazard_zone": {"type": "Feature", "properties": {}, "geometry": mapping(dataset.hazard_zone)},
        "population": {"type": "FeatureCollection", "features": population},
        "available_fields": list(dataset.scalar_fields),
    })


@app.route("/api/town/query", methods=["POST"])
def town_query():
    payload = request.get_json(force=True) or {}
    original_query = (payload.get("original_query") or payload.get("query") or "").strip()
    feedback = (payload.get("feedback") or "").strip()

    if not original_query:
        return jsonify({"error": "query is required"}), 400

    extra_context = ""
    if feedback:
        extra_context = prompts.RERUN_CONTEXT_TEMPLATE.format(
            feedback=feedback, original_query=original_query
        )

    agent = get_agent()
    try:
        result = agent.run_once(original_query, extra_context)
    except Exception as exc:  # LLM/backend failure -- surface it to the UI
        return jsonify({"error": str(exc)}), 502

    response = {
        "route": result["route"],
        "recommendation": result.get("recommendation"),
        "original_query": original_query,
    }

    if result["route"] == "geometry":
        response["tool_result"] = result.get("tool_result")
    elif result["route"] == "topology":
        response["integration"] = result.get("integration")
    elif result["route"] == "morse":
        field_name = result["field"]
        morse_result = result["morse_result"]
        field = agent.dataset.scalar_fields[field_name]
        response["field"] = field_name
        response["morse_summary"] = {
            "value_range": morse_result["value_range"],
            "persistence_threshold": morse_result["persistence_threshold"],
            "num_significant_basins": morse_result["num_significant_basins"],
        }
        response["geojson"] = {
            "field": field_to_geojson(field),
            "points": critical_points_to_geojson(morse_result),
        }

    return jsonify(response)


def _layer_summary(result: dict) -> dict:
    return {
        "value_range": result["value_range"],
        "persistence_threshold": result["persistence_threshold"],
        "num_significant_basins": result["num_significant_basins"],
        "num_significant_peak_basins": result["num_significant_peak_basins"],
        "euler_characteristic": result["euler_characteristic"],
    }


# -- Real-World Data: one bounding box, one topic, any number of extra ---
# user-supplied layers. The single entry point for actually fetching and
# analyzing anything in this panel -- Smart Query (below) only ever fills
# in the bounds/topic fields this endpoint reads, it never fetches on its
# own, so there is exactly one bounds/topic state and one "run" action.

@app.route("/api/realworld/analyze", methods=["POST"])
def realworld_analyze():
    bounds_raw = request.form.get("bounds", "")
    try:
        bounds = tuple(float(x) for x in bounds_raw.split(","))
        if len(bounds) != 4:
            raise ValueError
    except ValueError:
        return jsonify({"error": "bounds must be 'lat_min,lat_max,lon_min,lon_max'"}), 400

    try:
        resolution = int(request.form.get("resolution", 20))
    except ValueError:
        return jsonify({"error": "resolution must be an integer"}), 400
    resolution = max(6, min(resolution, 40))  # keep API/compute cost bounded

    primary_variable = request.form.get("primary_variable", "elevation")
    if primary_variable not in OPEN_METEO_VARIABLES:
        return jsonify({"error": f"unknown topic '{primary_variable}'"}), 400

    layer_sources: dict[str, str | None] = {primary_variable: None}
    layer_descriptions: dict[str, str] = {}
    tmp_paths = []
    for name in request.form.getlist("layer_name"):
        name = name.strip()
        if not name:
            continue
        file = request.files.get(f"layer_file::{name}")
        if file and file.filename:
            suffix = Path(file.filename).suffix.lower() or ".csv"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            file.save(tmp.name)
            tmp.close()
            tmp_paths.append(tmp.name)
            layer_sources[name] = tmp.name  # a layer named e.g. "elevation" overrides the live fetch above
        description = request.form.get(f"layer_description::{name}", "").strip()
        if description:
            layer_descriptions[name] = description

    if request.form.get("use_sample_layer") == "true":
        layer_sources["hazard_survey"] = str(APP_DIR / "examples" / "sample_hazard_layer.csv")

    try:
        dataset = build_real_world_dataset(
            "web_request", bounds, layer_sources, resolution=resolution,
            layer_descriptions=layer_descriptions,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        for p in tmp_paths:
            Path(p).unlink(missing_ok=True)

    results = {name: analyze_scalar_field(field) for name, field in dataset.scalar_fields.items()}

    recommendation = None
    if request.form.get("recommend") == "true" and primary_variable in results:
        try:
            llm = LLMClient()
            live_source = "a user-uploaded file" if layer_sources.get(primary_variable) else "the live Open-Meteo API (not pre-downloaded or cached)"
            context = (
                f"'{primary_variable}' data for this run came from {live_source}. Basins/peaks were "
                "computed by persistence-guided steepest-descent/ascent watershed directly on the raw "
                "field, with NO heuristic depression-filling or breaching preprocessing.\n"
                f"Morse analysis result: {json.dumps(results[primary_variable], ensure_ascii=False)}"
            )
            if layer_descriptions:
                context += (
                    "\nUser-supplied descriptions of other layers analyzed alongside this one "
                    f"(for context, not necessarily this result): {json.dumps(layer_descriptions, ensure_ascii=False)}"
                )
            recommendation = llm.chat(prompts.MORSE_RECOMMENDATION_SYSTEM, context)
        except Exception as exc:
            recommendation = f"(recommendation unavailable: {exc})"

    layers = {}
    for name, field in dataset.scalar_fields.items():
        result = results[name]
        layers[name] = {
            "field": field_to_geojson(field),
            "points": critical_points_to_geojson(result),
            "summary": _layer_summary(result),
        }

    lat_min, lat_max, lon_min, lon_max = bounds
    return jsonify({
        "bounds": [[lat_min, lon_min], [lat_max, lon_max]],
        "layers": layers,
        "recommendation": recommendation,
    })


# -- Smart Query: understand a free-text query, don't fetch anything -----
#
# An LLM extraction call (prompts.REALWORLD_QUERY_EXTRACTION_SYSTEM) works
# out what place/coordinates and which live variable (elevation,
# temperature, precipitation, wind, humidity, pressure -- see
# geoai_agent.real_data.OPEN_METEO_VARIABLES) a free-text query is about --
# natural-language phrasing (prepositions, capitalization, trailing
# qualifier clauses like "with a resolution of 30 meters") varies too much
# for a hand-written pattern to keep up with reliably. A named place is
# then live-geocoded to coordinates. This endpoint only ever returns what
# was understood -- it never fetches scalar-field data itself. The actual
# fetch+analyze is a single, separate action: /api/realworld/analyze,
# which the frontend fills in from this response's bounds/variable rather
# than triggering here, so there is exactly one bounds/topic state and one
# "run" button in the UI.

@app.route("/api/realworld/smart_query", methods=["POST"])
def realworld_smart_query():
    query = request.form.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        extraction = LLMClient().chat_json(prompts.REALWORLD_QUERY_EXTRACTION_SYSTEM, query)
    except Exception as exc:
        return jsonify({"error": f"query understanding failed: {exc}"}), 502

    variable = extraction.get("variable")
    if variable not in OPEN_METEO_VARIABLES:
        variable = "elevation"

    explicit_bounds = extraction.get("explicit_bounds")
    resolved_place = None
    if explicit_bounds and len(explicit_bounds) == 4:
        bounds = tuple(float(x) for x in explicit_bounds)
    else:
        place_name = extraction.get("place_name")
        geocoded = geocode_place(place_name) if place_name else None
        if geocoded is None:
            return jsonify({
                "error": (
                    f"Couldn't find a real-world location in that text"
                    f"{f' (understood it as \"{place_name}\")' if place_name else ''}. "
                    "Try naming a place more explicitly (e.g. \"...in Boulder, Colorado\"), "
                    "an explicit lat/lon box like '40.96N-41.15N, 75.15W-74.95W', "
                    "or use the manual Bounds field below instead."
                )
            }), 400
        bounds = bounding_box_around_point(geocoded["latitude"], geocoded["longitude"])
        resolved_place = geocoded

    lat_min, lat_max, lon_min, lon_max = bounds
    return jsonify({
        "bounds": [lat_min, lat_max, lon_min, lon_max],
        "variable": variable,
        "available_variables": list(OPEN_METEO_VARIABLES),
        "query": query,
        "resolved_place": resolved_place,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)

# GeoAI Agent -- Web UI

A Flask + vanilla JS/Leaflet front end over the GeoAI agent's analysis
engine. This folder is self-contained: `geoai_agent/` below is a standalone
copy of the routing/discrete-Morse-theory/real-world-data/GeoJSON package
(not an import of anything outside `web_app/`), and `examples/` holds its
own sample CSV layer. Nothing in this folder reaches outside it -- you can
copy just `web_app/` elsewhere and it still runs.

## Run

From inside `web_app/` (or from the project root, both work):

```
pip install -r requirements.txt
python app.py
```

Requires the same [Ollama](https://ollama.com) server as the rest of the
project (`qwen2.5:7b-instruct` pulled and running on
`http://localhost:11434`) for the LLM-driven routing and recommendation
steps. Open http://localhost:5000 once it's running.

## What's in each panel

**Town Scenario** -- type a free-text spatial question about the synthetic
town (same one `main.py` uses). It's routed through the full pipeline
(`geoai_agent/agent.py`): geometry tools, network topology, or discrete
Morse analysis over a scalar field, whichever the question needs. The map
always shows the town's road network, zones, hazard zone, and population;
a Morse-routed question additionally overlays that field's heatmap and
critical points, toggleable via the layer control. After a recommendation
comes back you can type feedback and re-run (same human-in-the-loop
re-run the CLI supports), or accept it.

**Real-World Data** -- enter a real lat/lon bounding box (defaults to the
Boulder, CO Flatirons) and it fetches genuine terrain elevation from the
free Open-Meteo API, derives a `slope` layer for free, and lets you add
any number of your own CSV layers (`x,y,value`, x=longitude/y=latitude) by
name + file upload -- the multi-layer path from
`geoai_agent/real_data.py`. Results render on a real OpenStreetMap
basemap with one heatmap + critical-points overlay per layer.

## API endpoints

- `GET /api/town/base_map` -- static GeoJSON for the town's roads, zones,
  hazard zone, and population points.
- `POST /api/town/query` -- `{"original_query": "...", "feedback": "..."}`
  (feedback optional) -> runs `GeoAIAgent.run_once`, returns the route,
  recommendation, and (for the morse route) field GeoJSON.
- `POST /api/realworld/analyze` -- multipart form: `bounds`, `resolution`,
  `use_sample_layer`, `recommend`, plus any number of
  `layer_name` + `layer_file::<name>` pairs for custom CSV layers -> runs
  `build_real_world_dataset` + `analyze_scalar_field` per layer, returns
  GeoJSON + summaries (+ an LLM recommendation for elevation if
  requested).

## Notes

- `app.run(debug=True)` is a development server -- fine for local use, not
  meant to be exposed to the internet as-is.
- Uploaded CSV layers are written to a temp file for the duration of one
  request and deleted immediately after.

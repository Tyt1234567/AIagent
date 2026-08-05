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

**Real-World Data** -- one bounding box, one topic (elevation, temperature,
precipitation, wind, humidity, or pressure -- live-fetched from Open-Meteo,
never pre-downloaded), and any number of extra user-uploaded layers, all
behind a single "Fetch & Analyze" button. Fill in the bounds/topic fields
either by hand, or describe the query in plain English ("find critical
points in Boulder, Colorado") and click "Parse query" -- an LLM extraction
step (natural-language phrasing varies too much for regex to keep up with
reliably) works out the place/coordinates and topic and fills the fields
in for you; it never fetches anything itself. Add your own CSV
(`x,y,value`) or shapefile (`.zip` bundling `.shp`/`.shx`/`.dbf`, POINT
geometry + a `value` field) layers by name + file upload -- naming a
custom layer the same as the selected topic (e.g. "elevation") overrides
the live fetch with your own data. Results render on a real OpenStreetMap
basemap with one heatmap + critical-points overlay per layer.

## API endpoints

- `GET /api/town/base_map` -- static GeoJSON for the town's roads, zones,
  hazard zone, and population points.
- `POST /api/town/query` -- `{"original_query": "...", "feedback": "..."}`
  (feedback optional) -> runs `GeoAIAgent.run_once`, returns the route,
  recommendation, and (for the morse route) field GeoJSON.
- `POST /api/realworld/smart_query` -- `{"query": "..."}` -> an LLM
  extraction call works out `{bounds, variable, resolved_place}` from free
  text; returns what was understood without fetching anything. The
  frontend fills the manual fields in from this response.
- `POST /api/realworld/analyze` -- multipart form: `bounds`, `resolution`,
  `primary_variable`, `use_sample_layer`, `recommend`, plus any number of
  `layer_name` + `layer_file::<name>` (+ optional `layer_description::<name>`)
  pairs for custom CSV/shapefile layers -> runs `build_real_world_dataset`
  + `analyze_scalar_field` per layer, returns GeoJSON + summaries (+ an
  LLM recommendation for `primary_variable` if requested). The single
  fetch+analyze action for this panel.

## Notes

- `app.run(debug=True)` is a development server -- fine for local use, not
  meant to be exposed to the internet as-is.
- Uploaded layer files are written to a temp file for the duration of one
  request and deleted immediately after.

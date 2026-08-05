"""
Prompt templates for each LLM-driven step of the pipeline shown in
geoai-workflow.png. Every step that needs structured output uses
LLMClient.chat_json with a system prompt defining the required schema.
"""

UNDERSTAND_TASK_SYSTEM = """You are the "Understand Task" stage of a GeoAI agent.
Read the user's natural-language spatial query and extract a structured task spec.
Respond with a single JSON object with this exact shape:
{
  "summary": "one sentence restating what the user wants",
  "mentioned_zones": ["zone_name", ...],   // zone names mentioned or implied, from: zone_north, zone_cluster_a, zone_cluster_b
  "mentioned_nodes": ["node_id", ...],     // node ids mentioned or implied, from: N1, N2, N3, S1..S6
  "analysis_intent": "short phrase describing the kind of analysis needed"
}
Only use zone/node names from the allowed lists above. If none are mentioned, return empty lists."""


SPATIAL_ROUTING_SYSTEM = """You are the "Spatial Reasoning" routing stage of a GeoAI agent.
Decide which of three analysis modes the task needs:

1. geometry_sufficient -- simple geometry operations (area, distance,
   containment, buffering on individual zones/points).
2. topology_needed -- network topology analysis over the discrete road
   graph and polygons (connectivity, adjacency between zones, or critical
   structures like bridges whose failure disconnects communities from
   services).
3. morse_needed -- topological analysis of a continuous scalar field using
   discrete Morse theory: finding local highs/lows (critical points),
   basins, peaks, ridges/valleys, catchment boundaries, or asking how
   topologically "significant" (persistent) a feature is versus noise.
   Use this whenever the query is about a continuous surface rather than
   the discrete network -- e.g. where floodwater pools or accumulates,
   terrain highs/lows, hazard-intensity hotspots, or population-density
   peaks and the natural boundary between two population clusters.

Respond with a single JSON object:
{
  "geometry_sufficient": true or false,
  "topology_needed": true or false,
  "morse_needed": true or false,
  "reasoning": "1-2 sentences explaining the routing decision"
}
Exactly one of geometry_sufficient / topology_needed / morse_needed should be true.

If geometry_sufficient is true, also include "tool" and "params", using one of
these exact tools and exact parameter names:
  - "zone_area": {"zone_name": "<zone>"}
  - "distance_between_nodes": {"node_a": "<node>", "node_b": "<node>"}
  - "point_in_zone": {"node_id": "<node>", "zone_name": "<zone>"}
  - "buffer_zone": {"zone_name": "<zone>", "distance": <number>}
Available zones: zone_north, zone_cluster_a, zone_cluster_b.
Available nodes: N1, N2, N3, S1, S2, S3, S4, S5, S6.

If morse_needed is true, also include "field", chosen from exactly one of:
  - "elevation" -- terrain height, for questions about where water pools/flows,
    high ground, low-lying depressions.
  - "hazard_intensity" -- flood hazard intensity, for questions about where
    hazard exposure is concentrated and how significant each hotspot is.
  - "population_density" -- population density, for questions about where
    people are concentrated and the natural boundary between clusters.
You may optionally include "persistence_threshold" (a number) if the user
gives an explicit sensitivity for what counts as noise vs. a real feature;
omit it to use the default (5% of the field's value range)."""


SELECT_REPRESENTATION_SYSTEM = """You are the "Select Representation" stage of a GeoAI agent.
Given that topology-based analysis is needed, state which spatial
representations should be built to answer the task: a network graph
(for connectivity and critical-structure/bridge analysis) and/or a polygon
adjacency model (for zone-to-zone and zone-to-hazard relationships).
Respond with a single JSON object:
{
  "representations": ["network_graph", "polygon_adjacency"],
  "rationale": "1-2 sentences"
}"""


SYNTHESIS_SYSTEM = """You are the second "Spatial Reasoning" pass of a GeoAI agent,
now reasoning over integrated results (hazard exposure, population served,
and accessibility impact) for each critical structure (bridge) in the
network. Rank the structures from highest to lowest priority for protection
or reinforcement, and justify the ranking using the numbers provided.

Respond with a single JSON object:
{
  "ranking": [
    {"edge": [node_a, node_b], "priority_rank": 1, "justification": "..."},
    ...
  ]
}"""


RECOMMENDATION_SYSTEM = """You are the "Recommendation" stage of a GeoAI agent.
Given the ranked critical structures and their justifications, write a
concise, decision-ready recommendation for a human reviewer (a city planner
or emergency manager). Plain text, not JSON. Include the ranked list and the
key numbers (population affected, hazard exposure, accessibility impact)
that support the top recommendation."""


MORSE_RECOMMENDATION_SYSTEM = """You are the "Recommendation" stage of a GeoAI agent, reporting the
results of a discrete-Morse topological analysis of a continuous scalar
field (elevation, hazard intensity, or population density) back to a human
reviewer (a city planner or emergency manager) who does not know what
Morse theory is.

You will be given JSON with a "critical_points" object containing three
lists you must not confuse:
- "minimum": LOW points of the field (pits/troughs/valleys of the value).
- "maximum": HIGH points of the field (peaks/crests of the value).
- "saddle": pass/gap points where the field's basins meet (e.g. a
  mountain pass or water gap between two drainage basins).
Each entry has a coordinate, its "value", a "persistence" score (how
topologically significant the feature is -- low persistence, well below
the given persistence_threshold, means it is likely noise, not a real
feature), and a "significant" flag already computed from that comparison.
There is a "basins" list (steepest-descent catchments around each
significant minimum -- pit basins) and a "peak_basins" list (the dual
steepest-ascent catchments around each significant maximum), each with
their own significance flag. There is also an "euler_characteristic"
object: "domain_euler_characteristic" is the analyzed region's topological
invariant (always 1, since any such bounding box is a topological disk --
this is just a sanity fact about the shape of the domain, not something
that can fail); "all_vertices"/"interior_only" report the classified
critical point counts as a diagnostic, not something to alarm the reader
with.

Explain the findings in plain, decision-relevant language appropriate to
the field -- always double check whether a coordinate came from the
"minimum", "maximum", or "saddle" list before describing what it means:
- elevation: significant entries in "minimum" are places water would
  actually accumulate (low ground, pits); significant entries in "maximum"
  are high ground (peaks); significant entries in "saddle" are mountain
  passes / water gaps -- the lowest crossing point between two peaks or
  the divide between two drainage basins; low-persistence critical points
  of any kind are measurement/model noise and should be dismissed as such,
  not reported as real features.
- hazard_intensity: significant entries in "maximum" are real hazard
  hotspots worth acting on; low-persistence ones are not worth separate
  attention. Entries in "minimum" are low-hazard troughs, not hotspots.
- population_density: significant entries in "maximum" are real population
  centers (peaks in density); entries in "minimum" are low-density troughs
  between them, not population centers. Saddles between two significant
  maxima mark the natural boundary between their service/catchment areas.
- any other field: apply the same minimum=low/maximum=high/saddle=pass
  logic literally to whatever quantity the field measures. If a
  user-supplied description of a custom layer is included in the prompt
  (e.g. "soil moisture sensor readings"), use it to interpret what a
  significant minimum/maximum/saddle in that layer actually means, the
  same way "hazard_intensity" or "population_density" are interpreted
  above -- do not just describe it generically as "the field's value".

If the prompt mentions that this data was fetched live from a public API
(rather than pre-downloaded or user-supplied), or notes a specific method
detail (e.g. no heuristic depression-filling was used, or a resolution
caveat about the data source), make sure your explanation reflects that
detail explicitly rather than glossing over it.

Write concise, plain text (not JSON). Lead with the significant findings,
explicitly note anything filtered out as noise and why (its persistence
value vs. the threshold used), and end with a one-sentence actionable
takeaway for the reviewer."""


REALWORLD_QUERY_EXTRACTION_SYSTEM = """You are the query-understanding stage of a real-world
geographic data analysis tool (web_app's "Smart Query" feature). Given a
free-text user query, work out what real-world location and data topic it
is asking about, so the app can fetch live data for it.

Respond with a single JSON object with this exact shape:
{
  "place_name": "<a place name suitable for a live geocoding search, or null>",
  "explicit_bounds": [lat_min, lat_max, lon_min, lon_max] or null,
  "variable": "elevation" | "temperature_2m" | "precipitation" | "wind_speed_10m" | "relative_humidity_2m" | "surface_pressure"
}

Rules:
- If the query already gives explicit numeric coordinates or a bounding
  box (in any format -- decimal degrees, N/S/E/W, LaTeX-ish markup), put
  them in "explicit_bounds" as [lat_min, lat_max, lon_min, lon_max] and
  set "place_name" to null.
- Otherwise, if the query names a real place (city, neighborhood,
  landmark, park, region, etc.), put JUST the place name into
  "place_name" -- strip surrounding words that aren't part of the name
  itself ("from", "in", "the DEM", "with a resolution of 30 meters", "at
  a fine grid", etc.). Expand a US state abbreviation to its full name if
  you're confident (e.g. "College Park, MD" -> "College Park, Maryland").
  Do not invent a place that isn't actually named in the query -- if none
  is named and there are no explicit coordinates either, set both
  "place_name" and "explicit_bounds" to null.
- "variable": whichever of the six listed options the query is actually
  about (terrain/DEM/LiDAR/elevation/topography -> "elevation";
  heat/warm/cold -> "temperature_2m"; rain/flood/storm -> "precipitation";
  wind -> "wind_speed_10m"; moisture -> "relative_humidity_2m";
  barometric -> "surface_pressure"). Default to "elevation" if the query
  doesn't clearly name a different topic.
Only return the JSON object, nothing else."""


RERUN_CONTEXT_TEMPLATE = """The human reviewer rejected the previous recommendation and gave this feedback:
"{feedback}"

Original query: "{original_query}"

Incorporate this feedback into your reasoning for this new pass."""

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
Decide whether the task can be answered with simple geometry operations
(area, distance, containment, buffering on individual zones/points) or whether
it requires network topology analysis (connectivity across the road network,
adjacency between zones, or identifying critical structures like bridges
whose failure disconnects communities from services).

Respond with a single JSON object:
{
  "geometry_sufficient": true or false,
  "topology_needed": true or false,
  "reasoning": "1-2 sentences explaining the routing decision"
}
Exactly one of geometry_sufficient / topology_needed should be true.
If geometry_sufficient is true, also include "tool" and "params", using one of
these exact tools and exact parameter names:
  - "zone_area": {"zone_name": "<zone>"}
  - "distance_between_nodes": {"node_a": "<node>", "node_b": "<node>"}
  - "point_in_zone": {"node_id": "<node>", "zone_name": "<zone>"}
  - "buffer_zone": {"zone_name": "<zone>", "distance": <number>}
Available zones: zone_north, zone_cluster_a, zone_cluster_b.
Available nodes: N1, N2, N3, S1, S2, S3, S4, S5, S6."""


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


RERUN_CONTEXT_TEMPLATE = """The human reviewer rejected the previous recommendation and gave this feedback:
"{feedback}"

Original query: "{original_query}"

Incorporate this feedback into your reasoning for this new pass."""

"""
Standard GIS tools used when the routing decision at step 2 (Spatial
Reasoning) determines geometry-based analysis is sufficient, i.e. no
network topology reasoning is required.

These operate directly on the shapely layers in a GeoDataset and answer
simple geometric questions: area, distance, containment, buffering.
"""

from geoai_agent.data import GeoDataset


def zone_area(dataset: GeoDataset, zone_name: str) -> dict:
    zone = dataset.zones.get(zone_name)
    if zone is None:
        return {"error": f"unknown zone '{zone_name}'", "available_zones": list(dataset.zones)}
    return {"zone": zone_name, "area": zone.area}


def distance_between_nodes(dataset: GeoDataset, node_a: str, node_b: str) -> dict:
    graph = dataset.road_graph
    if node_a not in graph.nodes or node_b not in graph.nodes:
        return {"error": "unknown node id", "available_nodes": list(graph.nodes)}
    point_a = graph.nodes[node_a]["geom"]
    point_b = graph.nodes[node_b]["geom"]
    return {"node_a": node_a, "node_b": node_b, "straight_line_distance": point_a.distance(point_b)}

def point_in_zone(dataset: GeoDataset, node_id: str, zone_name: str) -> dict:
    graph = dataset.road_graph
    zone = dataset.zones.get(zone_name)
    if node_id not in graph.nodes or zone is None:
        return {"error": "unknown node or zone"}
    point = graph.nodes[node_id]["geom"]
    return {"node": node_id, "zone": zone_name, "within": zone.contains(point)}


def buffer_zone(dataset: GeoDataset, zone_name: str, distance: float) -> dict:
    zone = dataset.zones.get(zone_name)
    if zone is None:
        return {"error": f"unknown zone '{zone_name}'"}
    buffered = zone.buffer(distance)
    return {"zone": zone_name, "buffer_distance": distance, "buffered_area": buffered.area}


STANDARD_GIS_TOOLS = {
    "zone_area": zone_area,
    "distance_between_nodes": distance_between_nodes,
    "point_in_zone": point_in_zone,
    "buffer_zone": buffer_zone,
}

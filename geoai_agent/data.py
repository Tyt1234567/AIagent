"""
Synthetic GIS dataset for the GeoAI Agent demo.

Scenario: a small town split by a river. Two residential clusters on the
south bank (A and B) each reach the hospital on the north bank through a
single bridge. A flood hazard zone covers the area around Bridge A.

Layers:
- road_graph: networkx.Graph of intersections/nodes and road segments/edges.
  Each edge carries a 'geom' (shapely LineString), 'length' (weight) and an
  'is_bridge_structure' flag marking the two river-crossing road segments.
- hazard_zone: shapely Polygon representing a flood-prone area.
- zones: dict of named shapely Polygons (residential/service zones) used
  for adjacency analysis.
- population_points: list of (shapely Point, population, nearest_node_id).
- hospital_node: the node id representing the critical service (hospital).
"""

from dataclasses import dataclass

import networkx as nx
from shapely.geometry import LineString, Point, Polygon

HOSPITAL_NODE = "N1"


@dataclass
class PopulationPoint:
    point: Point
    population: int
    nearest_node: str


def _add_road(graph: nx.Graph, u: str, v: str, coords_u, coords_v, is_bridge: bool = False) -> None:
    line = LineString([coords_u, coords_v])
    graph.add_edge(
        u,
        v,
        geom=line,
        length=line.length,
        is_bridge_structure=is_bridge,
    )


def build_road_graph() -> nx.Graph:
    graph = nx.Graph()

    coords = {
        # north bank backbone (hospital + two junctions)
        "N1": (0, 10),
        "N2": (10, 10),
        "N3": (20, 10),
        # south cluster A
        "S1": (0, 0),
        "S2": (-3, -3),
        "S3": (3, -3),
        # south cluster B
        "S4": (20, 0),
        "S5": (17, -3),
        "S6": (23, -3),
    }
    for node, xy in coords.items():
        graph.add_node(node, geom=Point(xy))

    # North bank backbone, forms a cycle so no single internal edge is
    # a true "bridge" (graph-theory sense) other than the river crossings.
    _add_road(graph, "N1", "N2", coords["N1"], coords["N2"])
    _add_road(graph, "N2", "N3", coords["N2"], coords["N3"])
    _add_road(graph, "N1", "N3", coords["N1"], coords["N3"])

    # South cluster A, internal loop.
    _add_road(graph, "S1", "S2", coords["S1"], coords["S2"])
    _add_road(graph, "S2", "S3", coords["S2"], coords["S3"])
    _add_road(graph, "S3", "S1", coords["S3"], coords["S1"])

    # South cluster B, internal loop.
    _add_road(graph, "S4", "S5", coords["S4"], coords["S5"])
    _add_road(graph, "S5", "S6", coords["S5"], coords["S6"])
    _add_road(graph, "S6", "S4", coords["S6"], coords["S4"])

    # River crossings: the only links between each south cluster and the
    # north bank / hospital. These are the physical bridges.
    _add_road(graph, "N1", "S1", coords["N1"], coords["S1"], is_bridge=True)
    _add_road(graph, "N3", "S4", coords["N3"], coords["S4"], is_bridge=True)

    return graph


def build_hazard_zone() -> Polygon:
    # Flood corridor around x in [-5, 5], covering Bridge A (N1-S1) but not
    # Bridge B (N3-S4) at x=20.
    return Polygon([(-5, -1), (5, -1), (5, 11), (-5, 11)])


def build_zones() -> dict[str, Polygon]:
    return {
        "zone_north": Polygon([(-2, 8), (22, 8), (22, 12), (-2, 12)]),
        "zone_cluster_a": Polygon([(-6, -5), (5, -5), (5, 1), (-6, 1)]),
        "zone_cluster_b": Polygon([(15, -5), (26, -5), (26, 1), (15, 1)]),
    }


def build_population_points() -> list[PopulationPoint]:
    return [
        PopulationPoint(Point(0, 0), 800, "S1"),
        PopulationPoint(Point(-3, -3), 1200, "S2"),
        PopulationPoint(Point(3, -3), 600, "S3"),
        PopulationPoint(Point(20, 0), 500, "S4"),
        PopulationPoint(Point(17, -3), 900, "S5"),
        PopulationPoint(Point(23, -3), 700, "S6"),
    ]


class GeoDataset:
    """Bundles all synthetic layers used by the pipeline for one run."""

    def __init__(self) -> None:
        self.road_graph = build_road_graph()
        self.hazard_zone = build_hazard_zone()
        self.zones = build_zones()
        self.population_points = build_population_points()
        self.hospital_node = HOSPITAL_NODE

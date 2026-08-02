"""
Step 5: Integrate Results.

Combines the raw topology_tools outputs (connectivity, adjacency, critical
structures) with domain layers to produce three integrated views per
critical structure:

- Hazard: does the structure sit inside the flood hazard zone?
- Population: how many people rely on this structure to reach the
  hospital?
- Accessibility: how much worse (or impossible) does the hospital trip
  become if this structure is removed?

The output of this step is the input to the second Spatial Reasoning pass
(step 6), which the LLM uses to rank and justify a recommendation.
"""

import networkx as nx

from geoai_agent.data import GeoDataset
from geoai_agent.topology_tools import run_topology_analysis


def _accessibility_without_edge(dataset: GeoDataset, edge: tuple[str, str]) -> dict[str, float | None]:
    reduced = dataset.road_graph.copy()
    reduced.remove_edge(*edge)
    impacts = {}
    for pop in dataset.population_points:
        try:
            impacts[pop.nearest_node] = nx.shortest_path_length(
                reduced, source=pop.nearest_node, target=dataset.hospital_node, weight="length"
            )
        except nx.NetworkXNoPath:
            impacts[pop.nearest_node] = None  # None means hospital becomes unreachable
    return impacts


def integrate_results(dataset: GeoDataset) -> dict:
    topology = run_topology_analysis(dataset)
    baseline_paths = topology["connectivity"]["shortest_path_to_hospital"]

    population_by_node = {pop.nearest_node: pop.population for pop in dataset.population_points}

    integrated_structures = []
    for structure in topology["critical_structures"]["critical_structures"]:
        edge = structure["edge"]
        in_hazard_zone = dataset.hazard_zone.intersects(structure["geom"])

        after = _accessibility_without_edge(dataset, edge)
        population_affected = 0
        max_delta = 0.0
        disconnected_nodes = []
        for node, after_length in after.items():
            before_length = baseline_paths.get(node)
            if after_length is None:
                population_affected += population_by_node.get(node, 0)
                disconnected_nodes.append(node)
            elif before_length is not None and after_length > before_length:
                population_affected += population_by_node.get(node, 0)
                max_delta = max(max_delta, after_length - before_length)

        integrated_structures.append({
            "edge": edge,
            "hazard": {"in_flood_zone": in_hazard_zone},
            "population": {
                "population_affected": population_affected,
                "disconnected_nodes": disconnected_nodes,
            },
            "accessibility": {
                "max_extra_distance_if_removed": max_delta,
                "fully_disconnected": len(disconnected_nodes) > 0,
            },
        })

    return {
        "topology": topology,
        "integrated_structures": integrated_structures,
    }

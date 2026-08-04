"""
Core orchestrator: implements the pipeline in geoai-workflow.png.

    User -> GeoAI Agent (LLM)
         -> [1] Understand Task
         -> [2] Spatial Reasoning (routing)
              geometry_sufficient -> Standard GIS Tools -> (done)
              topology_needed     -> [3] Select Representation
                                   -> [4] Topology Analysis (connectivity, adjacency, critical structures)
                                   -> [5] Integrate Results (hazard, population, accessibility)
                                   -> [6] Spatial Reasoning (synthesis)
                                   -> [7] Recommendation
                                   -> Human (feedback) -> Agent Re-runs -> back to GeoAI Agent
              morse_needed        -> [4] Morse Topology Analysis (critical points, persistence, basins
                                       on a continuous scalar field -- see morse_topology.py)
                                   -> [7] Recommendation
                                   -> Human (feedback) -> Agent Re-runs -> back to GeoAI Agent
"""

import json

from geoai_agent.data import GeoDataset
from geoai_agent.geometry_tools import STANDARD_GIS_TOOLS
from geoai_agent.integration import integrate_results
from geoai_agent.llm_client import LLMClient
from geoai_agent.topology_tools import analyze_scalar_topology
from geoai_agent import prompts


def _log(step: str, payload) -> None:
    print(f"\n--- {step} ---")
    if isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def _serialize_integration(integration: dict) -> dict:
    """Drop shapely geometry objects and convert edge tuples to lists so the
    result can be embedded in an LLM prompt as JSON."""
    structures = []
    for item in integration["integrated_structures"]:
        structures.append({
            "edge": list(item["edge"]),
            "hazard": item["hazard"],
            "population": item["population"],
            "accessibility": item["accessibility"],
        })
    connectivity = integration["topology"]["connectivity"]
    adjacency = integration["topology"]["adjacency"]
    return {
        "connectivity": connectivity,
        "adjacency": adjacency,
        "integrated_structures": structures,
    }


class GeoAIAgent:
    def __init__(self, llm_client: LLMClient | None = None, dataset: GeoDataset | None = None):
        self.llm = llm_client or LLMClient()
        self.dataset = dataset or GeoDataset()

    def run(self, user_query: str) -> None:
        """Top level loop: runs the pipeline, then hands the result to a
        human for review. On rejection feedback, re-runs the whole pipeline
        with the feedback folded into context (dashed 'Agent Re-runs' loop
        in the diagram)."""
        query = user_query
        extra_context = ""
        while True:
            result = self._run_pipeline(query, extra_context)

            if result["route"] == "geometry":
                # Geometry branch exits directly to Standard GIS Tools output,
                # there is no human review step on this path in the diagram.
                return

            _log("Recommendation", result["recommendation"])
            _log("Human", "Review the recommendation above.")
            feedback = input(
                "Approve this recommendation? Press Enter to accept, "
                "or type feedback to trigger a new pass: "
            ).strip()

            if not feedback:
                print("\nRecommendation accepted. Done.")
                return

            _log("Agent Re-runs", f"Re-running with feedback: {feedback}")
            extra_context = prompts.RERUN_CONTEXT_TEMPLATE.format(
                feedback=feedback, original_query=user_query
            )

    def _run_pipeline(self, query: str, extra_context: str = "") -> dict:
        task_spec = self._understand_task(query, extra_context)
        routing = self._spatial_reasoning_routing(query, task_spec, extra_context)

        if routing.get("geometry_sufficient"):
            self._run_standard_gis_tool(routing)
            return {"route": "geometry"}

        if routing.get("morse_needed"):
            return self._run_morse_analysis(routing, extra_context)

        representation = self._select_representation(task_spec)
        _log("3 Select Representation", representation)

        integration = integrate_results(self.dataset)
        public_integration = _serialize_integration(integration)
        _log("4 Topology Analysis", {
            "connectivity": public_integration["connectivity"],
            "adjacency": public_integration["adjacency"],
        })
        _log("5 Integrate Results", public_integration["integrated_structures"])

        synthesis = self._spatial_reasoning_synthesis(public_integration, extra_context)
        recommendation = self._recommendation(synthesis, public_integration, extra_context)

        return {"route": "topology", "recommendation": recommendation}

    # -- individual pipeline steps -----------------------------------

    def _understand_task(self, query: str, extra_context: str) -> dict:
        user_prompt = query if not extra_context else f"{query}\n\n{extra_context}"
        task_spec = self.llm.chat_json(prompts.UNDERSTAND_TASK_SYSTEM, user_prompt)
        _log("1 Understand Task", task_spec)
        return task_spec

    def _spatial_reasoning_routing(self, query: str, task_spec: dict, extra_context: str) -> dict:
        user_prompt = (
            f"User query: {query}\n"
            f"Task spec: {json.dumps(task_spec, ensure_ascii=False)}\n"
            f"{extra_context}"
        )
        routing = self.llm.chat_json(prompts.SPATIAL_ROUTING_SYSTEM, user_prompt)
        _log("2 Spatial Reasoning (routing)", routing)
        return routing

    def _run_standard_gis_tool(self, routing: dict) -> None:
        tool_name = routing.get("tool")
        params = routing.get("params", {})
        tool = STANDARD_GIS_TOOLS.get(tool_name)
        if tool is None:
            _log("Standard GIS Tools", {"error": f"unknown tool '{tool_name}'"})
            return
        try:
            result = tool(self.dataset, **params)
        except TypeError as exc:
            result = {"error": f"invalid arguments for '{tool_name}': {exc}", "params_received": params}
        _log("Standard GIS Tools", result)

    def _run_morse_analysis(self, routing: dict, extra_context: str) -> dict:
        field_name = routing.get("field", "elevation")
        threshold = routing.get("persistence_threshold")
        result = analyze_scalar_topology(self.dataset, field_name, persistence_threshold=threshold)
        _log("4 Morse Topology Analysis", result)
        recommendation = self._morse_recommendation(result, extra_context)
        return {"route": "morse", "recommendation": recommendation}

    def _morse_recommendation(self, morse_result: dict, extra_context: str) -> str:
        user_prompt = f"Morse analysis result: {json.dumps(morse_result, ensure_ascii=False)}\n{extra_context}"
        recommendation = self.llm.chat(prompts.MORSE_RECOMMENDATION_SYSTEM, user_prompt)
        return recommendation

    def _select_representation(self, task_spec: dict) -> dict:
        user_prompt = f"Task spec: {json.dumps(task_spec, ensure_ascii=False)}"
        return self.llm.chat_json(prompts.SELECT_REPRESENTATION_SYSTEM, user_prompt)

    def _spatial_reasoning_synthesis(self, public_integration: dict, extra_context: str) -> dict:
        user_prompt = (
            f"Integrated results: {json.dumps(public_integration['integrated_structures'], ensure_ascii=False)}\n"
            f"{extra_context}"
        )
        synthesis = self.llm.chat_json(prompts.SYNTHESIS_SYSTEM, user_prompt)
        _log("6 Spatial Reasoning (synthesis)", synthesis)
        return synthesis

    def _recommendation(self, synthesis: dict, public_integration: dict, extra_context: str) -> str:
        user_prompt = (
            f"Ranking: {json.dumps(synthesis, ensure_ascii=False)}\n"
            f"Supporting data: {json.dumps(public_integration['integrated_structures'], ensure_ascii=False)}\n"
            f"{extra_context}"
        )
        return self.llm.chat(prompts.RECOMMENDATION_SYSTEM, user_prompt)

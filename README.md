# GeoAI Agent

An agentic pipeline that answers natural-language spatial questions by routing
between simple geometry tools and full network-topology analysis, then
produces a human-reviewed recommendation. Implements the pipeline diagrammed
in `geoai-workflow.png`.

```
User -> GeoAI Agent (LLM)
     -> [1] Understand Task
     -> [2] Spatial Reasoning (routing)
          geometry_sufficient -> Standard GIS Tools -> done
          topology_needed     -> [3] Select Representation
                               -> [4] Topology Analysis (connectivity, adjacency, critical structures)
                               -> [5] Integrate Results (hazard, population, accessibility)
                               -> [6] Spatial Reasoning (synthesis)
                               -> [7] Recommendation
                               -> Human (feedback) -> Agent Re-runs -> back to GeoAI Agent
```

## Scenario

The agent reasons over a synthetic town split by a river (`geoai_agent/data.py`):
two residential clusters, each reachable from the hospital by exactly one
bridge. A flood hazard zone covers one of the two bridges. This setup lets
the agent weigh hazard exposure, population served, and accessibility loss
against each other when recommending which bridge to protect first.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with `qwen2.5:7b-instruct` pulled:
  ```
  ollama pull qwen2.5:7b-instruct
  ```
- Python packages:
  ```
  pip install -r requirements.txt
  ```

## Usage

```
python main.py "Which bridge should we reinforce first, considering flood risk and the population that depends on it to reach the hospital?"
```

Or run without arguments to pick from example queries interactively:

```
python main.py
```

The pipeline prints each step's output as it runs. When the topology branch
reaches step 7, it prints a `Recommendation` and prompts for human review:

- Press Enter to accept and finish.
- Type feedback (e.g. `"ignore flood risk, focus on accessibility only"`) to
  trigger a new pass — the agent re-runs the full pipeline with your
  feedback folded into its reasoning.

A geometry-sufficient query (e.g. `"What is the area of zone_cluster_a?"`)
routes directly to `Standard GIS Tools` and exits without a human review
step, matching the diagram.

## Project layout

| File | Role |
|---|---|
| `geoai_agent/data.py` | Synthetic GIS dataset: road graph, hazard polygon, zones, population points |
| `geoai_agent/geometry_tools.py` | Standard GIS tools for the geometry-sufficient branch (area, distance, containment, buffer) |
| `geoai_agent/topology_tools.py` | Connectivity, adjacency, and critical-structure (graph bridge) analysis |
| `geoai_agent/integration.py` | Combines topology output with hazard/population/accessibility per structure |
| `geoai_agent/llm_client.py` | OpenAI-compatible client targeting a local Ollama server |
| `geoai_agent/prompts.py` | System prompts and JSON schemas for each LLM-driven step |
| `geoai_agent/agent.py` | Orchestrator implementing the full pipeline and the human feedback re-run loop |
| `main.py` | CLI entry point |

## Configuration

The LLM backend defaults to `http://localhost:11434/v1` with model
`qwen2.5:7b-instruct`. To use a different OpenAI-compatible endpoint (e.g.
DeepSeek), construct `GeoAIAgent` with a custom `LLMClient`:

```python
from geoai_agent.agent import GeoAIAgent
from geoai_agent.llm_client import LLMClient

llm = LLMClient(model="deepseek-chat", base_url="https://api.deepseek.com", api_key="...")
agent = GeoAIAgent(llm_client=llm)
agent.run("your query")
```

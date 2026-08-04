# GeoAI Agent

An agentic pipeline that answers natural-language spatial questions by routing
between simple geometry tools, network-topology analysis, and continuous
scalar-field topology (discrete Morse theory), then produces a
human-reviewed recommendation. Implements the pipeline diagrammed in
`geoai-workflow.png`, extended with a third routing branch.

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
          morse_needed         -> [4] Morse Topology Analysis (critical points, persistence, watershed basins
                                    on a continuous scalar field)
                               -> [7] Recommendation
                               -> Human (feedback) -> Agent Re-runs -> back to GeoAI Agent
```

## Scenario

The agent reasons over a synthetic town split by a river (`geoai_agent/data.py`):
two residential clusters, each reachable from the hospital by exactly one
bridge. A flood hazard zone covers one of the two bridges. This setup lets
the agent weigh hazard exposure, population served, and accessibility loss
against each other when recommending which bridge to protect first.

The same dataset also carries three continuous scalar fields sampled on a
grid over the town (synthetic terrain elevation, flood hazard intensity, and
population density). These feed the `morse_needed` branch, which runs a
from-scratch discrete Morse theory engine (`geoai_agent/morse_topology.py`)
to find critical points (basins, peaks, saddles) and rank them by
topological persistence, so the agent can tell a real feature (e.g. an
actual flood-accumulation basin) from noise (a boundary artifact or a minor
bump). The approach follows the discrete-Morse / persistence-based terrain
analysis methodology of the UMIACS GeoVis Lab (https://geovis.umiacs.io/,
e.g. `FormanGradient2D`, `Terrain_Trees`), reimplemented natively in Python
so it runs on any scalar field, not just terrain.

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
python main.py "Where would floodwater actually pool in this town, and how significant are those low points compared to noise?"
python main.py "Where are the population density peaks, and what's the natural boundary between the two residential clusters?"
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
step, matching the diagram. A `morse_needed` query goes through the same
human review loop as the topology branch.

## Project layout

| File | Role |
|---|---|
| `geoai_agent/data.py` | Synthetic GIS dataset: road graph, hazard polygon, zones, population points, and scalar fields (elevation, hazard intensity, population density) |
| `geoai_agent/geometry_tools.py` | Standard GIS tools for the geometry-sufficient branch (area, distance, containment, buffer) |
| `geoai_agent/topology_tools.py` | Connectivity, adjacency, and critical-structure (graph bridge) analysis over the discrete network |
| `geoai_agent/morse_topology.py` | Discrete Morse theory over continuous scalar fields: critical point classification, persistence pairing, watershed segmentation |
| `geoai_agent/integration.py` | Combines topology output with hazard/population/accessibility per structure |
| `geoai_agent/llm_client.py` | OpenAI-compatible client targeting a local Ollama server |
| `geoai_agent/prompts.py` | System prompts and JSON schemas for each LLM-driven step |
| `geoai_agent/agent.py` | Orchestrator implementing the full pipeline and the human feedback re-run loop |
| `main.py` | CLI entry point |

## Scalar field topology (discrete Morse theory)

`geoai_agent/morse_topology.py` is a generic engine: it takes any
`ScalarField` (a named grid of values) and returns:

- **Critical points** -- minima, maxima, and saddles, classified via
  connected components of each vertex's lower/upper link (the standard PL
  Morse definition).
- **Persistence** -- each critical point is paired with the saddle that
  would merge/split its feature, via a union-find sweep over sub/superlevel
  sets (a merge tree and its dual split tree). The persistence value is how
  much the field would have to change to erase that feature -- low
  persistence means noise, high persistence means a real, load-bearing
  feature of the surface.
- **Watershed basins** -- every grid cell is assigned to a basin by
  steepest descent to a local minimum, then basins below the persistence
  threshold are merged into their spill partner, giving a simplified,
  noise-free segmentation.

It is not tied to terrain: the same code runs on `elevation`,
`hazard_intensity`, `population_density`, or any other field later added to
`GeoDataset.scalar_fields`. `geoai_agent/topology_tools.analyze_scalar_topology`
is the thin dataset-aware entry point the agent calls.

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

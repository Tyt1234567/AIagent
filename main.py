"""
CLI entry point for the GeoAI Agent Spatial Reasoning Pipeline.

Prerequisites (see local_llm_example.py):
1. Ollama installed and running (http://localhost:11434).
2. Model pulled: ollama pull qwen2.5:7b-instruct
3. pip install -r requirements.txt

Usage:
    python main.py
    python main.py "Which bridge should we reinforce first, considering flood risk?"
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from geoai_agent.agent import GeoAIAgent

EXAMPLE_QUERIES = [
    "Which bridge should we reinforce first, considering flood risk and the population that depends on it to reach the hospital?",
    "What is the area of zone_cluster_a?",
]


def main() -> None:
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        print("No query given on the command line. Example queries:")
        for example in EXAMPLE_QUERIES:
            print(f"  - {example}")
        query = input("\nEnter your spatial query: ").strip()
        if not query:
            query = EXAMPLE_QUERIES[0]
            print(f"Using default example query:\n  {query}")

    agent = GeoAIAgent()
    agent.run(query)


if __name__ == "__main__":
    main()

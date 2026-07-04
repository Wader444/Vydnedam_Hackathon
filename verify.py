"""
verify.py
=========
Database health check for the ImpactGraph Graph Engine.
Confirms connectivity and node/relationship population.

Reads credentials exclusively from environment variables (.env) —
never hardcodes passwords.
"""

import sys
import os
import logging

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Load .env before reading any env vars
load_dotenv()

# ─── Credentials — always from environment, never hardcoded ──────────────────
URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
AUTH     = (
    os.getenv("NEO4J_USER",     "neo4j"),
    os.getenv("NEO4J_PASSWORD", "password123"),
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("neo4j").setLevel(logging.WARNING)


def verify_graph_engine() -> None:
    """
    Verify Neo4j connectivity and graph population.

    Exits with code 1 on any failure so CI pipelines can detect problems.
    All resources (driver, session) are guaranteed to close via try/finally.
    """
    # ── Step 1: Establish and verify connection ───────────────────────────────
    driver = None
    try:
        driver = GraphDatabase.driver(URI, auth=AUTH)
        driver.verify_connectivity()
        print("[SUCCESS] Connected to Neo4j successfully.")
    except Exception as e:
        print("[ERROR] Could not connect to Neo4j.")
        print(f"Details: {e}")
        print(
            "\n[HELP] Check that your Docker container is running:\n"
            "  docker-compose up -d\n"
            "  Verify port 7687 is not already allocated by another process."
        )
        if driver:
            driver.close()
        sys.exit(1)

    # ── Step 2: Query node and relationship counts ────────────────────────────
    try:
        with driver.session() as session:
            # Count all nodes
            node_result = session.run("MATCH (n) RETURN count(n) AS count")
            node_count: int = node_result.single()["count"]

            # Count all directed relationships
            edge_result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
            edge_count: int = edge_result.single()["count"]

            if node_count == 0:
                print("[WARNING] Connected to Neo4j, but the database is EMPTY.")
                print(
                    "\n[HELP] Run ingestion first:\n"
                    "  .\\scripts\\load_graph.ps1 -JsonPath data\\sample_contract.json"
                )
            else:
                print(
                    f"[SUCCESS] Graph populated! "
                    f"Found {node_count} nodes and {edge_count} relationships."
                )

                # ── Step 3: Show one sample schema edge for sanity ────────────
                print("\n[INFO] Schema Sample:")
                sample_result = session.run(
                    "MATCH (n)-[r]->(m) "
                    "RETURN labels(n)[0] AS source, type(r) AS rel, labels(m)[0] AS target "
                    "LIMIT 1"
                )
                sample = sample_result.single()
                if sample:
                    print(f"   ({sample['source']}) -[{sample['rel']}]-> ({sample['target']})")
                else:
                    print(
                        "   [WARNING] Nodes exist but NO RELATIONSHIPS found. "
                        "Graph may be partially ingested."
                    )

    except Exception as e:
        print(f"[ERROR] Query execution failed: {e}")
        sys.exit(1)

    finally:
        # Guaranteed close — even if an exception occurs mid-session
        driver.close()


if __name__ == "__main__":
    verify_graph_engine()
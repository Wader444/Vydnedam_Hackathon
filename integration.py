"""
integration.py
==============
Person 1 (Graph Engineer) ↔ Person 3 (LLM/AI Layer) Integration Script
ImpactGraph | Vydnedam Hackathon

Pipeline position:
    codebase_graph.json  (Person 2 output)
        ↓
    ingest_codebase_graph()   ← populates Neo4j via MERGE
        ↓
    get_blast_radius(fn_name) ← traverses up to 4 hops of incoming CALLS
        ↓
    Person 3 (LLM prompt construction)

JSON ↔ Cypher data model:
    JSON field          Neo4j property
    ─────────────────────────────────
    function["name"]  → Function.name   (node identity key for MERGE)
    function["file"]  → Function.file   (source file path, metadata only)
    function["calls"] → [:CALLS] edge   (directed: callee → caller reversed;
                                         "a calls b" ⟹ (a)-[:CALLS]->(b))
"""

import json
import logging
import os
from typing import Any, Dict, List

from dotenv import load_dotenv
from neo4j import GraphDatabase, Driver

# ─── Logging ──────────────────────────────────────────────────────────────────
# Suppress noisy third-party library output; only our messages appear.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("integration")

# Silence neo4j driver's own INFO/DEBUG chatter so console stays clean.
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("neo4j.io").setLevel(logging.WARNING)
logging.getLogger("neo4j.pool").setLevel(logging.WARNING)

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

# Connection parameters: pulled from .env file if present, else fall back to
# the standard Docker-launched Neo4j defaults used across the team.
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")


# ─── Connection Helper ────────────────────────────────────────────────────────

def _get_driver() -> Driver:
    """
    Build and verify a Neo4j driver instance.

    Raises:
        ConnectionError: if Neo4j is unreachable at the configured URI.
    """
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        logger.info("Neo4j connection established at %s", NEO4J_URI)
        return driver
    except Exception as exc:
        raise ConnectionError(
            f"Cannot reach Neo4j at {NEO4J_URI}. "
            "Is the Docker container running? "
            f"Details: {exc}"
        ) from exc


# ─── Ingestion ────────────────────────────────────────────────────────────────

def ingest_codebase_graph(json_path: str, clear_existing: bool = False) -> None:
    """
    Read a codebase_graph.json file (Person 2 output) and populate Neo4j.

    Two-pass strategy
    ─────────────────
    Pass 1 – Nodes:
        MERGE every Function by name.  Using MERGE (not CREATE) means re-running
        this function on the same or updated JSON is always safe — no duplicates.
        The `file` property is set/updated on every run so it tracks renames.

    Pass 2 – Edges:
        For every "calls" reference in the JSON, MERGE a directed [:CALLS] edge
        from the caller Function node to the callee Function node.  If Person 2
        lists a callee that has no own entry in the JSON (e.g. a stdlib call),
        a minimal Function node is created on-the-fly so the edge isn't dropped.

    Args:
        json_path:       Path to codebase_graph.json.
        clear_existing:  When True, wipes ALL nodes/rels before ingesting.
                         Useful for clean hackathon demo resets.
    """
    # ── 1. Load JSON ──────────────────────────────────────────────────────────
    logger.info("Loading JSON from: %s", json_path)
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            data: Dict[str, Any] = json.load(fh)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"codebase_graph.json not found at '{json_path}'. "
            "Has Person 2 dropped the file yet?"
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"codebase_graph.json is malformed JSON: {exc}") from exc

    functions: List[Dict[str, Any]] = data.get("functions", [])
    if not functions:
        logger.warning("JSON contains no 'functions' array — nothing to ingest.")
        return

    logger.info("Parsed %d function records from JSON.", len(functions))

    # ── 2. Open driver ────────────────────────────────────────────────────────
    driver = _get_driver()

    try:
        with driver.session() as session:

            # ── 2a. Optional clean slate ──────────────────────────────────────
            if clear_existing:
                logger.warning("Clearing all existing nodes and relationships …")
                session.run("MATCH (n) DETACH DELETE n")
                logger.info("Graph cleared.")

            # ── 2b. Pass 1: MERGE Function nodes ─────────────────────────────
            # UNWIND sends the entire list in a single round-trip to Neo4j,
            # which is dramatically faster than one Cypher call per function.
            #
            # JSON mapping:
            #   fn["name"] → Function.name  (unique identifier / MERGE key)
            #   fn["file"] → Function.file  (metadata: where it lives on disk)
            node_query = """
            UNWIND $functions AS fn
            MERGE (f:Function {name: fn.name})
            ON CREATE SET f.file = fn.file
            ON MATCH  SET f.file = fn.file
            """
            session.run(node_query, functions=[
                {"name": fn["name"], "file": fn.get("file", "unknown")}
                for fn in functions
            ])
            logger.info("Pass 1 complete: Function nodes merged.")

            # ── 2c. Pass 2: MERGE [:CALLS] relationships ──────────────────────
            # We flatten the JSON into a list of (caller, callee) pairs and
            # UNWIND them in one shot.
            #
            # JSON mapping:
            #   fn["name"]     → caller Function.name
            #   fn["calls"][i] → callee Function.name
            #
            # MERGE on the callee node guarantees that even if Person 2 omitted
            # a callee from the top-level "functions" array (e.g. external libs),
            # the graph edge is still created — no silent data loss.
            edges: List[Dict[str, str]] = []
            for fn in functions:
                caller_name = fn["name"]
                for callee_name in fn.get("calls", []):
                    edges.append({"caller": caller_name, "callee": callee_name})

            if edges:
                edge_query = """
                UNWIND $edges AS edge
                MERGE (caller:Function {name: edge.caller})
                MERGE (callee:Function {name: edge.callee})
                MERGE (caller)-[:CALLS]->(callee)
                """
                session.run(edge_query, edges=edges)
                logger.info("Pass 2 complete: %d [:CALLS] relationships merged.", len(edges))
            else:
                logger.info("Pass 2: No call edges found in JSON.")

    finally:
        driver.close()

    logger.info("Ingestion complete.")


# ─── Blast Radius Query ───────────────────────────────────────────────────────

def get_blast_radius(changed_function_name: str, max_depth: int = 4) -> List[Dict[str, str]]:
    """
    Return every Function that depends on `changed_function_name` — directly
    or transitively — up to `max_depth` hops away via incoming [:CALLS] edges.

    This answers the product's core question:
        "If THIS function changes, what else breaks?"

    Cypher pattern:
        MATCH (changed:Function {name: $func_name})
              <-[:CALLS*1..4]-
              (dependent:Function)
        RETURN dependent.name AS name, dependent.file AS file

    The arrow direction matters:
        (A)-[:CALLS]->(B)  means "A calls B"
        So B changing means A is at risk → we traverse *into* B looking for A.

    Args:
        changed_function_name: The function being modified.
        max_depth:             How many hops of transitive callers to follow.
                               Default 4 is wide enough for most codebases.

    Returns:
        A list of dicts, each with keys "name" (str) and "file" (str).
        Empty list if the function has no callers or does not exist in the graph.
    """
    driver = _get_driver()

    # Build depth range dynamically so callers can tune without touching Cypher.
    depth_range = f"1..{int(max_depth)}"
    cypher = f"""
    MATCH (changed:Function {{name: $func_name}})<-[:CALLS*{depth_range}]-(dependent:Function)
    RETURN DISTINCT dependent.name AS name,
                    dependent.file AS file
    ORDER BY dependent.file, dependent.name
    """

    results: List[Dict[str, str]] = []
    try:
        with driver.session() as session:
            records = session.run(cypher, func_name=changed_function_name)
            for rec in records:
                results.append({
                    "name": rec["name"],
                    "file": rec["file"] or "unknown",
                })
    finally:
        driver.close()

    return results


# ─── Execution Guard ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    JSON_FILE = "codebase_graph.json"
    TARGET_FUNCTION = "calculate_total"

    # Step 1: Ingest the JSON.
    # Set clear_existing=True for a clean demo reset on every run.
    ingest_codebase_graph(JSON_FILE, clear_existing=True)

    # Step 2: Query blast radius immediately after ingestion.
    blast_radius = get_blast_radius(TARGET_FUNCTION)

    # Step 3: Print clean output for Person 3 handoff verification.
    print(f"\n--- Blast Radius for '{TARGET_FUNCTION}' ---")
    if blast_radius:
        for dep in blast_radius:
            print(dep)
    else:
        print("(No dependents found — function is a leaf node or not in graph.)")
    print(f"\nTotal affected functions: {len(blast_radius)}")

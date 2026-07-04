"""
impact_api.py
=============
Person 3 (LLM/AI Layer) Handoff Interface for ImpactGraph
Vydnedam Hackathon

This module is the single import point for Person 3.
It wraps the Neo4j graph engine with high-level functions that:
  - Return structured impact metadata as clean Python objects
  - Generate ready-to-inject LLM prompt strings
  - Provide complete dependency context for any function in the graph

Usage by Person 3:
    from impact_api import get_impact_summary, format_for_llm_prompt

Pipeline position:
    Neo4j Graph DB
        ↓
    impact_api.py   ← THIS FILE
        ↓
    Person 3: LLM Prompt → AI Analysis → Developer Report
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase, Driver

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("impact_api")

# Suppress neo4j driver noise
logging.getLogger("neo4j").setLevel(logging.WARNING)

# ─── Environment ─────────────────────────────────────────────────────────────
load_dotenv()

NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")


# ─── Internal Driver Helper ───────────────────────────────────────────────────

def _driver() -> Driver:
    """
    Returns a verified Neo4j driver instance.
    Always call driver.close() after use, or use as context manager.
    """
    try:
        drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        drv.verify_connectivity()
        return drv
    except Exception as exc:
        raise ConnectionError(
            f"Neo4j unreachable at {NEO4J_URI}. Is Docker running? Detail: {exc}"
        ) from exc


def _run_read(cypher: str, **params: Any) -> List[Dict[str, Any]]:
    """
    Execute a read-only Cypher query and return results as plain Python dicts.
    Handles driver lifecycle cleanly so callers never leak connections.
    """
    drv = _driver()
    try:
        with drv.session() as session:
            records = session.run(cypher, **params)
            return [dict(rec) for rec in records]
    finally:
        drv.close()


# ─── Core API Functions ───────────────────────────────────────────────────────

def get_direct_callers(function_name: str) -> List[Dict[str, str]]:
    """
    Returns functions that call `function_name` exactly once (1 hop).

    Person 3 use case: "Who directly depends on this function right now?"

    Returns:
        List of {"name": str, "file": str}
    """
    cypher = """
    MATCH (caller:Function)-[:CALLS]->(target:Function {name: $fn})
    RETURN DISTINCT caller.name AS name,
                    coalesce(caller.file, 'unknown') AS file
    ORDER BY file, name
    """
    return _run_read(cypher, fn=function_name)


def get_blast_radius(function_name: str, max_depth: int = 4) -> List[Dict[str, str]]:
    """
    Returns ALL functions transitively dependent on `function_name`,
    up to `max_depth` hops away through incoming [:CALLS] edges.

    This is the core impact propagation query powering the AI analysis.

    Person 3 use case: "If this function changes, what is the full blast radius?"

    Args:
        function_name: The function being changed / analysed.
        max_depth:     Maximum traversal depth (default 4 hops).

    Returns:
        List of {"name": str, "file": str}, deduplicated and sorted by file.
    """
    depth = f"1..{int(max_depth)}"
    cypher = f"""
    MATCH (target:Function {{name: $fn}})<-[:CALLS*{depth}]-(dependent:Function)
    RETURN DISTINCT dependent.name AS name,
                    coalesce(dependent.file, 'unknown') AS file
    ORDER BY file, name
    """
    return _run_read(cypher, fn=function_name)


def get_callees(function_name: str) -> List[Dict[str, str]]:
    """
    Returns functions that `function_name` directly calls (outgoing edges).

    Person 3 use case: "What does this function depend on? (its own dependencies)"

    Returns:
        List of {"name": str, "file": str}
    """
    cypher = """
    MATCH (source:Function {name: $fn})-[:CALLS]->(callee:Function)
    RETURN DISTINCT callee.name AS name,
                    coalesce(callee.file, 'unknown') AS file
    ORDER BY file, name
    """
    return _run_read(cypher, fn=function_name)


def get_full_dependency_map(function_name: str, max_depth: int = 4) -> Dict[str, Any]:
    """
    Builds the full 360-degree dependency context for a given function.

    Combines:
        - The function's own metadata (file, line ranges if present)
        - What it calls (outgoing dependencies)
        - What calls it directly (direct callers)
        - Its full transitive blast radius (all impacted functions)
        - Which files are affected
        - Which classes contain the impacted functions

    Person 3 use case: Complete context block for LLM prompt construction.

    Returns:
        {
            "function": {"name": str, "file": str},
            "calls":    [{"name": str, "file": str}, ...],
            "direct_callers": [...],
            "blast_radius":   [...],
            "affected_files": [str, ...],
            "affected_classes": [{"id": str, "name": str, "file": str}, ...]
        }
    """
    # ── Function node metadata ────────────────────────────────────────────────
    meta_cypher = """
    MATCH (fn:Function {name: $fn})
    RETURN fn.name AS name,
           coalesce(fn.file, 'unknown') AS file,
           fn.id AS id,
           fn.start_line AS start_line,
           fn.end_line AS end_line
    LIMIT 1
    """
    meta = _run_read(meta_cypher, fn=function_name)
    function_meta = meta[0] if meta else {"name": function_name, "file": "unknown"}

    # ── What it calls ─────────────────────────────────────────────────────────
    callees = get_callees(function_name)

    # ── Who calls it directly ─────────────────────────────────────────────────
    direct_callers = get_direct_callers(function_name)

    # ── Full blast radius ─────────────────────────────────────────────────────
    blast = get_blast_radius(function_name, max_depth)

    # ── Derive affected files from blast radius ────────────────────────────────
    all_affected = [function_meta] + blast
    affected_files = sorted({
        entry["file"] for entry in all_affected
        if entry.get("file") and entry["file"] != "unknown"
    })

    # ── Classes containing any impacted function ──────────────────────────────
    # Only meaningful when data was ingested via the full contract schema
    # (impactgraph_schema_sample.json), not the simple codebase_graph.json.
    blast_names = [b["name"] for b in blast] + [function_name]
    classes_cypher = """
    MATCH (c:Class)-[:HAS_METHOD]->(fn:Function)
    WHERE fn.name IN $names
    RETURN DISTINCT c.id AS id, c.name AS name,
                    coalesce(c.file, 'unknown') AS file
    ORDER BY file, name
    """
    drv = _driver()
    affected_classes: List[Dict[str, Any]] = []
    try:
        with drv.session() as session:
            records = session.run(classes_cypher, names=blast_names)
            affected_classes = [dict(rec) for rec in records]
    finally:
        drv.close()

    return {
        "function":         function_meta,
        "calls":            callees,
        "direct_callers":   direct_callers,
        "blast_radius":     blast,
        "affected_files":   affected_files,
        "affected_classes": affected_classes,
    }


def get_graph_stats() -> Dict[str, Any]:
    """
    Returns high-level graph topology statistics.

    Person 3 use case: Include in LLM system prompt as codebase context.

    Returns:
        {"node_count": int, "relationship_count": int,
         "function_count": int, "file_count": int, "class_count": int}
    """
    cypher = """
    CALL () { MATCH (n:Function) RETURN count(n) AS function_count }
    CALL () { MATCH (n:File)     RETURN count(n) AS file_count     }
    CALL () { MATCH (n:Class)    RETURN count(n) AS class_count    }
    CALL () { MATCH ()-[r:CALLS]-()  RETURN count(r) AS call_edges    }
    CALL () { MATCH ()-[r:IMPORTS]-() RETURN count(r) AS import_edges }
    RETURN function_count, file_count, class_count, call_edges, import_edges
    """
    results = _run_read(cypher)
    return results[0] if results else {}


# ─── LLM Prompt Generator ─────────────────────────────────────────────────────

def format_for_llm_prompt(function_name: str, max_depth: int = 4) -> str:
    """
    Generates a complete, structured LLM prompt string describing the impact
    of changing `function_name`.

    Person 3 passes this string directly into the LLM as the user/context message.
    The format is plain English + code-style lists for maximum model comprehension.

    Args:
        function_name: The function being analysed.
        max_depth:     Traversal depth for blast radius.

    Returns:
        A multi-line string ready to embed into an LLM prompt.
    """
    context = get_full_dependency_map(function_name, max_depth)
    stats   = get_graph_stats()
    fn      = context["function"]
    ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Build section blocks ──────────────────────────────────────────────────
    def _list_block(items: List[Dict[str, str]], name_key: str = "name", file_key: str = "file") -> str:
        if not items:
            return "  (none)"
        return "\n".join(
            f"  - {item[name_key]}  [{item.get(file_key, 'unknown')}]"
            for item in items
        )

    callees_block        = _list_block(context["calls"])
    direct_callers_block = _list_block(context["direct_callers"])
    blast_block          = _list_block(context["blast_radius"])
    files_block          = "\n".join(f"  - {f}" for f in context["affected_files"]) or "  (none)"
    classes_block        = _list_block(context["affected_classes"], name_key="name") \
                           if context["affected_classes"] else "  (none — or not using class schema)"

    graph_context = (
        f"  Functions: {stats.get('function_count', '?')} | "
        f"Files: {stats.get('file_count', '?')} | "
        f"Classes: {stats.get('class_count', '?')} | "
        f"Call edges: {stats.get('call_edges', '?')} | "
        f"Import edges: {stats.get('import_edges', '?')}"
    )

    prompt = f"""
==========================================================================
  IMPACTGRAPH — SOFTWARE CHANGE IMPACT ANALYSIS
  Generated: {ts}
  Target Function: `{fn['name']}`  [{fn.get('file', 'unknown')}]
==========================================================================

CODEBASE GRAPH STATISTICS:
{graph_context}

──────────────────────────────────────────────────────────────────────────
SECTION 1: WHAT `{fn['name']}` CALLS (its own dependencies)
──────────────────────────────────────────────────────────────────────────
If any of these functions ALSO change, `{fn['name']}` may break:

{callees_block}

──────────────────────────────────────────────────────────────────────────
SECTION 2: DIRECT CALLERS (1 hop — immediately affected)
──────────────────────────────────────────────────────────────────────────
These functions call `{fn['name']}` directly and WILL be affected:

{direct_callers_block}

──────────────────────────────────────────────────────────────────────────
SECTION 3: FULL BLAST RADIUS (up to {max_depth} hops — transitively affected)
──────────────────────────────────────────────────────────────────────────
If `{fn['name']}` changes, ALL of the following functions may break:

{blast_block}

──────────────────────────────────────────────────────────────────────────
SECTION 4: AFFECTED FILES (must be reviewed / tested)
──────────────────────────────────────────────────────────────────────────
{files_block}

──────────────────────────────────────────────────────────────────────────
SECTION 5: AFFECTED CLASSES
──────────────────────────────────────────────────────────────────────────
{classes_block}

──────────────────────────────────────────────────────────────────────────
TASK FOR AI:
──────────────────────────────────────────────────────────────────────────
Given the dependency graph above, please:

1. Summarise the RISK LEVEL of changing `{fn['name']}` (Low / Medium / High / Critical).
2. List the TOP 3 most dangerous downstream effects in plain English.
3. Recommend which files and tests should be reviewed before merging this change.
4. Flag any circular dependency patterns or architectural concerns visible in the graph.
==========================================================================
""".strip()

    return prompt


def get_impact_summary(function_name: str, max_depth: int = 4) -> Dict[str, Any]:
    """
    Returns a single structured summary dict suitable for JSON serialisation
    and LLM API calls (e.g. as a tool response or structured message content).

    Person 3 use case: Feeding into OpenAI / Gemini function-calling or
    structured output modes.

    Returns:
        {
            "function_name": str,
            "source_file": str,
            "blast_radius_count": int,
            "affected_files_count": int,
            "risk_level": str,         # heuristic: Low/Medium/High/Critical
            "blast_radius": [...],
            "affected_files": [...],
            "direct_callers": [...],
            "calls": [...]
        }
    """
    dep_map = get_full_dependency_map(function_name, max_depth)
    blast_count = len(dep_map["blast_radius"])
    files_count = len(dep_map["affected_files"])

    # ── Heuristic risk level ──────────────────────────────────────────────────
    # These thresholds are intentionally conservative for hackathon demos.
    if blast_count == 0:
        risk = "Low"
    elif blast_count <= 3:
        risk = "Medium"
    elif blast_count <= 8:
        risk = "High"
    else:
        risk = "Critical"

    return {
        "function_name":        function_name,
        "source_file":          dep_map["function"].get("file", "unknown"),
        "blast_radius_count":   blast_count,
        "affected_files_count": files_count,
        "risk_level":           risk,
        "blast_radius":         dep_map["blast_radius"],
        "affected_files":       dep_map["affected_files"],
        "affected_classes":     dep_map["affected_classes"],
        "direct_callers":       dep_map["direct_callers"],
        "calls":                dep_map["calls"],
    }


# ─── Demo Runner ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Force UTF-8 output on Windows terminals that default to cp1252
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    """
    End-to-end demo of the full Person 3 handoff interface.

    Assumes Neo4j is running and data has been ingested.
    Run integration.py first if the database is empty.
    """
    TARGET = "calculate_total"

    print("\n" + "=" * 70)
    print(" IMPACT API — PERSON 3 HANDOFF DEMO")
    print("=" * 70)

    # ── 1. Structured summary (JSON-serialisable) ─────────────────────────────
    print(f"\n[1] IMPACT SUMMARY for '{TARGET}':\n")
    summary = get_impact_summary(TARGET)
    print(json.dumps(summary, indent=2))

    # ── 2. Full LLM prompt string ─────────────────────────────────────────────
    print(f"\n[2] LLM PROMPT STRING for '{TARGET}':\n")
    prompt = format_for_llm_prompt(TARGET)
    print(prompt)

    print("\n" + "=" * 70)
    print(f" Risk Level : {summary['risk_level']}")
    print(f" Blast Radius : {summary['blast_radius_count']} functions affected")
    print(f" Files at Risk : {summary['affected_files_count']} files")
    print("=" * 70 + "\n")

# ImpactGraph — Graph Engine: Final Audit Report
**Branch:** `graph-engine` | **Repo:** `Wader444/Vydnedam_Hackathon`
**Author:** Person 1 — Graph Engineer
**Audit Date:** 2026-07-04
**Auditor Role:** Principal Software Engineer / QA Lead
**Status:** CLEARED FOR PHASE 3 INTEGRATION

---

## 1. Architecture Summary

### How the Python Driver Connects to Local Neo4j

```
Developer Machine
|
+-- Docker Desktop
|   +-- neo4j:5-community container
|       +-- Bolt port 7687  <- Python driver connects here
|       +-- HTTP port 7474  <- Browser UI
|
+-- Python Environment (venv)
    +-- neo4j-python-driver (neo4j==5.x)
        +-- GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", <pw>))
            +-- driver.session() -> session.run(cypher, params)
```

**Connection lifecycle:**
1. `load_dotenv()` reads `.env` -> sets `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
2. `GraphDatabase.driver(URI, auth=(USER, PASS))` creates a connection pool
3. `driver.verify_connectivity()` performs a lightweight ping
4. All session work is scoped inside `with driver.session() as session:` blocks
5. `driver.close()` is called inside a `finally:` block — guaranteed even on exception

**Credentials:** Exclusively loaded from `.env` — never hardcoded in any source file.

---

## 2. Frozen Graph Schema

### 2.1 Node Labels and Properties

| Label    | Merge / Unique Key | Properties                                    | Required              |
|----------|--------------------|-----------------------------------------------|-----------------------|
| File     | path               | path (str), language (str)                   | path, language        |
| Function | id / name          | id, name, file, start_line, end_line         | id, name, file        |
| Class    | id                 | id, name, file                               | id, name, file        |

> **Dual-schema note:** `graph/ingest.py` (full schema) merges Functions by `id`. `integration.py` (simple schema) merges Functions by `name`. Both are valid for their respective JSON contracts.

### 2.2 Relationship Types

| Relationship | Direction                             | Properties                          |
|--------------|---------------------------------------|-------------------------------------|
| DEFINES      | (File)->(Function) or (File)->(Class) | none                                |
| IMPORTS      | (File)->(File)                        | none                                |
| HAS_METHOD   | (Class)->(Function)                   | none                                |
| CALLS        | (Function)->(Function)                | line_number (int), call_type (enum) |

### 2.3 call_type Enum (validated at ingestion)

```
"direct" | "method" | "library" | "recursive" | "async"
```

### 2.4 Uniqueness Constraints

```cypher
CREATE CONSTRAINT file_path_unique     IF NOT EXISTS FOR (f:File)     REQUIRE f.path IS UNIQUE;
CREATE CONSTRAINT function_id_unique   IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id   IS UNIQUE;
CREATE CONSTRAINT class_id_unique      IF NOT EXISTS FOR (c:Class)     REQUIRE c.id    IS UNIQUE;
```

---

## 3. API Contract for Person 3

**Import path:** `from impact_api import <function>`

### 3.1 get_blast_radius(function_name, max_depth=4)

```python
def get_blast_radius(function_name: str, max_depth: int = 4) -> List[Dict[str, str]]
```
Returns all functions transitively dependent on `function_name`.
**Returns:** `List[{"name": str, "file": str}]`

### 3.2 get_direct_callers(function_name)

```python
def get_direct_callers(function_name: str) -> List[Dict[str, str]]
```
Returns only the 1-hop callers.
**Returns:** `List[{"name": str, "file": str}]`

### 3.3 get_callees(function_name)

```python
def get_callees(function_name: str) -> List[Dict[str, str]]
```
Returns all functions that `function_name` directly calls.
**Returns:** `List[{"name": str, "file": str}]`

### 3.4 get_full_dependency_map(function_name, max_depth=4)

```python
def get_full_dependency_map(function_name: str, max_depth: int = 4) -> Dict[str, Any]
```
**Returns:**
```python
{
    "function":         {"name": str, "file": str, "start_line": int, "end_line": int},
    "calls":            List[{"name": str, "file": str}],
    "direct_callers":   List[{"name": str, "file": str}],
    "blast_radius":     List[{"name": str, "file": str}],
    "affected_files":   List[str],
    "affected_classes": List[{"id": str, "name": str, "file": str}]
}
```

### 3.5 get_impact_summary(function_name, max_depth=4)

```python
def get_impact_summary(function_name: str, max_depth: int = 4) -> Dict[str, Any]
```
**Returns:**
```python
{
    "function_name":        str,
    "source_file":          str,
    "blast_radius_count":   int,
    "affected_files_count": int,
    "risk_level":           "Low" | "Medium" | "High" | "Critical",
    "blast_radius":         List[{"name": str, "file": str}],
    "affected_files":       List[str],
    "affected_classes":     List[{"id": str, "name": str, "file": str}],
    "direct_callers":       List[{"name": str, "file": str}],
    "calls":                List[{"name": str, "file": str}]
}
```

**Risk heuristic:**

| blast_radius_count | risk_level |
|--------------------|------------|
| 0                  | Low        |
| 1-3                | Medium     |
| 4-8                | High       |
| 9+                 | Critical   |

### 3.6 format_for_llm_prompt(function_name, max_depth=4)

```python
def format_for_llm_prompt(function_name: str, max_depth: int = 4) -> str
```
Returns a ready-to-inject plain-text LLM prompt string with five structured sections and a task directive. UTF-8 safe. No side effects when imported as a module.

**Usage:**
```python
from impact_api import format_for_llm_prompt, get_impact_summary

prompt  = format_for_llm_prompt("calculate_total")  # inject into Claude/Gemini
summary = get_impact_summary("calculate_total")      # structured dict/JSON
```

### 3.7 get_graph_stats()

```python
def get_graph_stats() -> Dict[str, Any]
```
**Returns:** `{"function_count": int, "file_count": int, "class_count": int, "call_edges": int, "import_edges": int}`

---

## 4. Idempotency Confirmation

All write operations use Cypher MERGE exclusively — never CREATE.

### Node idempotency
```cypher
MERGE (f:Function {name: fn.name})
ON CREATE SET f.file = fn.file
ON MATCH  SET f.file = fn.file
```
If node exists -> updates properties only. If new -> creates it. Result: exactly one node per unique name.

### Edge idempotency
```cypher
MERGE (caller:Function {name: edge.caller})
MERGE (callee:Function {name: edge.callee})
MERGE (caller)-[:CALLS]->(callee)
```
MERGE on a relationship matches the exact (start)-[type]->(end) triple. If edge exists -> no action. Result: exactly one [:CALLS] edge per caller/callee pair.

### Empirical verification
```
Run 1:  8 Function nodes merged, 10 [:CALLS] relationships merged
Run 2:  8 Function nodes merged, 10 [:CALLS] relationships merged  <- identical
        0 new nodes created, 0 new edges created on second run
```

---

## 5. Audit Log — Issues Found and Fixed

### Issue 1 — verify.py: Hardcoded Credentials
**Severity:** HIGH (security)
**Finding:** URI and AUTH were literal string constants in source code.
**Fix:** Replaced with load_dotenv() + os.getenv() pattern.

### Issue 2 — verify.py: Unclosed Driver on Session Exception
**Severity:** HIGH (resource leak)
**Finding:** driver.close() was only reachable under normal flow. Mid-session exceptions leaked the Bolt connection pool.
**Fix:** driver.close() moved to finally: block — guaranteed on all paths.

### Issue 3 — verify.py: No try/except Around Session Queries
**Severity:** HIGH (unhandled crash)
**Finding:** session.run() calls had no exception handling. Schema mismatch would produce raw Python tracebacks.
**Fix:** All session operations wrapped in try/except with clean [ERROR] output and sys.exit(1).

### Issue 4 — integration.py: Hardcoded __main__ Variables
**Severity:** MEDIUM (maintainability)
**Finding:** JSON_FILE and TARGET_FUNCTION were hardcoded literals, not parameterisable.
**Fix:** Replaced with argparse. Now callable as:
```
python integration.py --json my_data.json --function my_func --clear
```

### Issue 5 — integration.py: Unguarded session.run() on Clear Step
**Severity:** MEDIUM (silent failure risk)
**Finding:** DETACH DELETE had no try/except. A connection drop mid-clear would leave graph in undefined state.
**Fix:** Wrapped in try/except with logger.error() and re-raise.

### Issue 6 — integration.py: Missing ingest_ast_data() Alias
**Severity:** MEDIUM (handoff contract)
**Finding:** Person 2's agreed function name was absent from the codebase.
**Fix:** Added ingest_ast_data() as an explicit alias with full docstring and JSON key documentation.

### Issue 7 — queries.py: Deprecated CALL {} Syntax
**Severity:** MEDIUM (stderr pollution)
**Finding:** get_dependency_statistics() used CALL { } without () scope clause, deprecated in Neo4j 5.x. Produces 01N00 warnings on stderr contaminating Person 3 CLI pipeline.
**Fix:** Updated all five CALL subqueries to CALL () { } syntax.

---

## 6. Handoff Verification

### ingest_ast_data() — JSON Key Compatibility with Person 2

| JSON key              | Mapped to           | Required | Notes                          |
|-----------------------|---------------------|----------|--------------------------------|
| functions[].name      | Function.name       | YES      | Must be unique across codebase |
| functions[].file      | Function.file       | YES      | Relative path string           |
| functions[].calls     | [:CALLS] edges      | YES      | List of callee name strings    |
| functions[].start_line| Function.start_line | NO       | Stored as metadata if present  |
| functions[].end_line  | Function.end_line   | NO       | Stored as metadata if present  |

Extra top-level keys ("classes", "files") are ignored safely.

### format_for_llm_prompt() — Output Validation

Test run against live Neo4j with calculate_total produced a clean, structured prompt with:
- 4 direct callers identified
- 6 blast radius functions identified
- 6 affected files listed
- Risk level: High
- Task directive for AI clearly separated

Result: Clean plain-text string. No binary characters. No print() side-effects on import. UTF-8 safe. Injectable into Claude/Gemini as-is. VERIFIED.

---

## 7. Final Test Results

```
============================= test session starts =============================
platform win32 -- Python 3.13.2, pytest-9.1.1
collected 6 items

tests/test_ingest.py::TestNeo4jIngestor::test_connect_failure            PASSED
tests/test_ingest.py::TestNeo4jIngestor::test_connect_success            PASSED
tests/test_ingest.py::TestNeo4jIngestor::test_ingestion_transaction_flow PASSED
tests/test_ingest.py::TestNeo4jIngestor::test_validation_invalid_call_type PASSED
tests/test_ingest.py::TestNeo4jIngestor::test_validation_missing_keys    PASSED
tests/test_ingest.py::TestNeo4jIngestor::test_validation_valid_json      PASSED

============================== 6 passed in 0.59s ==============================

verify.py output:
  [SUCCESS] Connected to Neo4j successfully.
  [SUCCESS] Graph populated! Found 8 nodes and 10 relationships.
  [INFO]    Schema Sample: (Function) -[CALLS]-> (Function)
```

---

## 8. Phase 3 Readiness Declaration

**This branch is CLEARED FOR PHASE 3 INTEGRATION.**

| Condition                                                         | Status |
|-------------------------------------------------------------------|--------|
| All unit tests passing (6/6)                                      | PASS   |
| No hardcoded credentials in any tracked file                     | PASS   |
| No Neo4j driver or session resource leaks                        | PASS   |
| All database operations wrapped in try/except                    | PASS   |
| No bare print() calls in importable library functions            | PASS   |
| ingest_ast_data() available for Person 2                         | PASS   |
| format_for_llm_prompt() returns clean injectable string          | PASS   |
| Idempotency confirmed — duplicate ingestion safe                 | PASS   |
| Neo4j 5.x syntax compliance (no deprecated CALL {})             | PASS   |
| .gitignore protecting .env and venv/                             | PASS   |
| Latest commit pushed to origin/graph-engine                      | PASS   |

**Person 3 integration is unblocked.**

```python
from impact_api import (
    get_blast_radius,
    get_direct_callers,
    get_callees,
    get_full_dependency_map,
    get_impact_summary,
    format_for_llm_prompt,
    get_graph_stats,
)
```

---

*Audit conducted and signed off by Person 1 — Graph Engine lead.*
*Vydnedam Hackathon 2026 | Team Codinjas*

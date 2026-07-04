# ImpactGraph - Graph Engine

ImpactGraph is an AI-powered Software Dependency and Impact Analysis platform. The **Graph Engine** serves as the central storage and traversal layer, parsing extracted repository dependencies and mapping them onto a high-performance Neo4j graph database.

Downstream LLM layers use this Graph Engine to answer critical developer questions such as:
> **"If this function changes, what functions, classes, and files are affected?"**

---

## Architecture Flow

```
[Repository Source]
       │
       ▼
[Static Extraction (Person 2)]
       │
       ▼
[JSON Dependency Payload]
       │
       ▼
[Graph Engine Ingestor (YOU)]  ◄── Uses transactions / MERGE
       │
       ▼
[Neo4j Graph Database]
       │
       ▼
[Query Engine API]             ◄── Exposes serializable dicts
       │
       ▼
[LLM/AI Reasoning (Person 3)]  ◄── Generates developer impact report
```

---

## Folder Structure

```
Graph-engine/
├── .env.example             # Template for Neo4j login configuration
├── docker-compose.yml       # Spins up Neo4j 5 database container with APOC
├── requirements.txt         # Package dependencies (neo4j driver, jsonschema, etc.)
├── README.md                # Standard developer onboarding documentation
├── data/
│   └── extracted_functions.json  # Sample raw code extract
├── docs/
│   └── graph_schema.md      # In-depth Node and Relationship properties specification
├── graph/
│   ├── __init__.py          # Python package initializer
│   ├── constraints.cypher   # Cypher constraints definition
│   ├── ingest.py            # JSON schema validation and transactional database writer
│   └── queries.py           # Dependency traversal and impact query interface
├── scripts/
│   └── load_graph.ps1       # PowerShell automation script for loading environment & running ingest
└── tests/
    └── test_ingest.py       # pytest suite using unit test mocks
```

---

## Quick Start Setup

### 1. Database Setup
Start the local Neo4j 5 Community instance using Docker Compose:

```bash
docker-compose up -d
```

This starts Neo4j on:
- **Bolt protocol**: `bolt://localhost:7687`
- **Neo4j Browser HTTP UI**: [http://localhost:7474](http://localhost:7474) (Username: `neo4j`, Password: `password123`)

### 2. Python Environment & Installation
Create a Python 3.12+ virtual environment and install packages:

```bash
python -m venv venv
venv\Scripts\activate      # On Windows
pip install -r requirements.txt
```

### 3. Environment Setup
Configure your Neo4j password in a `.env` file at the project root. Copy from `.env.example`:

```ini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password123
```

---

## Ingesting Code Dependencies

To import code metadata into Neo4j, pass the path of your extracted dependency JSON file.

### Option A: Using PowerShell (Windows)
Run the automation loader:
```powershell
.\scripts\load_graph.ps1 -JsonPath impactgraph_schema_sample.json
```

### Option B: Using Python directly
```bash
python -m graph.ingest impactgraph_schema_sample.json
```

---

## Example Ingest Payload Contract

The schema contract requires JSON objects following the layout below:

```json
{
  "files": [
    {
      "path": "auth.py",
      "language": "python",
      "imports": ["database.py"]
    }
  ],
  "functions": [
    {
      "id": "auth.py::authenticate_user",
      "name": "authenticate_user",
      "file": "auth.py",
      "start_line": 12,
      "end_line": 41,
      "calls": [
        {
          "id": "database.py::validate_token",
          "line_number": 18,
          "call_type": "direct"
        }
      ]
    }
  ],
  "classes": [
    {
      "id": "auth.py::UserManager",
      "name": "UserManager",
      "file": "auth.py",
      "methods": [
        "auth.py::authenticate_user"
      ]
    }
  ]
}
```

---

## Programming with the Query API

Downstream agents and components (such as Person 3's LLM engine) can query the database directly using `GraphQueryEngine`. It automatically maps Cypher data patterns into serializable Python dictionaries.

```python
from graph.queries import GraphQueryEngine

# Connect inside a context manager
with GraphQueryEngine() as engine:
    # 1. Fetch impacted files, classes, and functions from modification
    impact = engine.find_impacted_entities("auth.py::authenticate_user")
    print("Impacted Functions:", [f["name"] for f in impact["functions"]])
    print("Impacted Classes:", [c["name"] for c in impact["classes"]])
    print("Impacted Files:", [f["path"] for f in impact["files"]])

    # 2. Get dependency stats
    stats = engine.get_dependency_statistics()
    print("Total Files in Graph:", stats["file_count"])

    # 3. Export full impact tree
    tree = engine.export_impact_tree("auth.py::authenticate_user")
    import json
    print(json.dumps(tree, indent=2))
```

---

## Troubleshooting & Verification

### 1. Verify Uniqueness Constraints
If nodes are duplicating, make sure Neo4j uniqueness constraints are applied by checking the Neo4j Browser Cypher CLI:
```cypher
SHOW CONSTRAINTS;
```
You should see constraints on `:File(path)`, `:Function(id)`, and `:Class(id)`.

### 2. Manual Cypher Checks
*   **Find impact path from a function**:
    ```cypher
    MATCH path = (caller:Function)-[:CALLS*1..5]->(target:Function {id: 'auth.py::authenticate_user'})
    RETURN path
    ```
*   **Clear Graph completely**:
    ```cypher
    MATCH (n) DETACH DELETE n
    ```

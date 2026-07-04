import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Load environment variables
load_dotenv()

# Read settings
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

driver = None
neo4j_online = False

try:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    # Test connectivity immediately
    driver.verify_connectivity()
    neo4j_online = True
except Exception:
    # Neo4j is offline. We will gracefully fall back to local in-memory analysis.
    pass

def init_db():
    """
    Enforces constraints and initializes DB schema.
    """
    if not neo4j_online:
        return False

    try:
        with driver.session() as session:
            # Create constraints (compatible with Neo4j 4.x and 5.x)
            session.run("CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE")
            session.run("CREATE CONSTRAINT class_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE (c.name, c.file) IS UNIQUE")
            session.run("CREATE CONSTRAINT function_name_unique IF NOT EXISTS FOR (fn:Function) REQUIRE (fn.name, fn.file) IS UNIQUE")
        return True
    except Exception as e:
        print(f"Error initializing Neo4j constraints: {e}")
        return False

def populate_graph(data_list: dict):
    """
    Synchronizes parsed symbols (files, classes, functions, calls) into Neo4j.
    """
    if not neo4j_online:
        return False

    try:
        with driver.session() as session:
            # Clear previous nodes
            session.run("MATCH (n) DETACH DELETE n")

            # 1. Merge Files
            for f in data_list.get("files", []):
                session.run("MERGE (file:File {path: $path})", path=f["path"])

            # 2. Merge Classes & Defines
            for c in data_list.get("classes", []):
                session.run(
                    """
                    MERGE (class:Class {name: $name, file: $file})
                    WITH class
                    MATCH (file:File {path: $file})
                    MERGE (file)-[:DEFINES]->(class)
                    """,
                    name=c["name"], file=c["file"]
                )

            # 3. Merge Functions & contains/defines
            for fn in data_list.get("functions", []):
                session.run(
                    """
                    MERGE (func:Function {name: $name, file: $file})
                    """,
                    name=fn["name"], file=fn["file"]
                )

                if fn["class"]:
                    session.run(
                        """
                        MATCH (func:Function {name: $name, file: $file})
                        MATCH (class:Class {name: $class_name, file: $file})
                        MERGE (class)-[:CONTAINS]->(func)
                        """,
                        name=fn["name"], file=fn["file"], class_name=fn["class"]
                    )
                else:
                    session.run(
                        """
                        MATCH (func:Function {name: $name, file: $file})
                        MATCH (file:File {path: $file})
                        MERGE (file)-[:DEFINES]->(func)
                        """,
                        name=fn["name"], file=fn["file"]
                    )

            # 4. Draw directional CALLS relationships
            for fn in data_list.get("functions", []):
                for called_name in fn.get("calls", []):
                    session.run(
                        """
                        MATCH (source:Function {name: $source_name, file: $source_file})
                        MATCH (target:Function {name: $target_name})
                        MERGE (source)-[:CALLS]->(target)
                        """,
                        source_name=fn["name"],
                        source_file=fn["file"],
                        target_name=called_name
                    )
        return True
    except Exception as e:
        print(f"Error populating Neo4j graph: {e}")
        return False

def trace_downstream_deps(function_name: str, max_depth: int = 4) -> list[dict]:
    """
    Executes traversal queries to identify impacted downstream nodes.
    Supports both literal forward calls and caller-dependents.
    """
    if not neo4j_online:
        return trace_downstream_deps_fallback(function_name)

    results = {}
    
    # 1. Query 1: Literal direction matching (f:Function {name: $name})-[:CALLS*1..4]->(dependent:Function)
    query_forward = """
    MATCH path = (f:Function {name: $name})-[:CALLS*1..4]->(dependent:Function)
    RETURN dependent.name AS name, dependent.file AS file, length(path) AS distance
    ORDER BY distance
    """
    
    # 2. Query 2: Caller direction matching (dependent:Function)-[:CALLS*1..4]->(f:Function {name: $name})
    query_backward = """
    MATCH path = (dependent:Function)-[:CALLS*1..4]->(f:Function {name: $name})
    RETURN dependent.name AS name, dependent.file AS file, length(path) AS distance
    ORDER BY distance
    """

    try:
        with driver.session() as session:
            # Run forward trace
            res_f = session.run(query_forward, name=function_name)
            for record in res_f:
                key = (record["name"], record["file"])
                results[key] = {
                    "name": record["name"],
                    "file": record["file"],
                    "distance": record["distance"]
                }
                
            # Run backward trace (these are the true dependent callers!)
            res_b = session.run(query_backward, name=function_name)
            for record in res_b:
                key = (record["name"], record["file"])
                # Prefer shorter distance if already recorded
                if key not in results or record["distance"] < results[key]["distance"]:
                    results[key] = {
                        "name": record["name"],
                        "file": record["file"],
                        "distance": record["distance"]
                    }
    except Exception as e:
        print(f"Error querying Neo4j trace: {e}")
        return trace_downstream_deps_fallback(function_name)

    return list(results.values())

def trace_downstream_deps_fallback(function_name: str, directory: str = ".") -> list[dict]:
    """
    In-memory BFS fallback that traces dependencies when Neo4j is offline.
    """
    from parser_engine import parse_directory
    data = parse_directory(directory)

    # Build call connections map
    # calls_map: func_name -> list of called function names
    # callers_map: func_name -> list of functions that call it
    calls_map = {}
    callers_map = {}
    func_to_file = {}

    for fn in data.get("functions", []):
        name = fn["name"]
        calls = fn.get("calls", [])
        calls_map[name] = calls
        func_to_file[name] = fn["file"]
        
        for call in calls:
            if call not in callers_map:
                callers_map[call] = []
            callers_map[call].append(name)

    visited = {}
    
    # Run BFS on callers (incoming dependencies)
    queue = [(function_name, 0)]
    while queue:
        curr, depth = queue.pop(0)
        if depth >= 4:
            continue
            
        # Get callers of this function
        parents = callers_map.get(curr, [])
        for p in parents:
            if p not in visited:
                visited[p] = {
                    "name": p,
                    "file": func_to_file.get(p, "unknown.py"),
                    "distance": depth + 1
                }
                queue.append((p, depth + 1))

    # Run BFS on forward calls (outgoing dependencies)
    queue_f = [(function_name, 0)]
    while queue_f:
        curr, depth = queue_f.pop(0)
        if depth >= 4:
            continue
            
        children = calls_map.get(curr, [])
        for c in children:
            if c not in visited:
                visited[c] = {
                    "name": c,
                    "file": func_to_file.get(c, "unknown.py"),
                    "distance": depth + 1
                }
                queue_f.append((c, depth + 1))
            elif depth + 1 < visited[c]["distance"]:
                visited[c]["distance"] = depth + 1

    return list(visited.values())

if __name__ == "__main__":
    print(f"Neo4j Status: {'ONLINE' if neo4j_online else 'OFFLINE (Fallback Active)'}")
    if neo4j_online:
        init_db()
        print("Neo4j DB Constraints Initialized.")
    else:
        # Dry-run fallback test on itself
        deps = trace_downstream_deps_fallback("walk_tree")
        print("\nFallback tracing for 'walk_tree' in parser_engine:")
        for dep in deps:
            print(f" - {dep['name']} (distance: {dep['distance']}) in {dep['file']}")

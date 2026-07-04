"""
Query module for extracting dependency analysis and impact paths from Neo4j.
Exposes clean Python dictionaries and list-based methods suitable for direct LLM prompt inclusion.
"""

import os
import logging
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from neo4j import GraphDatabase, Driver, Record

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger("graph_queries")


class GraphQueryEngine:
    """
    Query Engine to run dependency, impact, and statistical queries on Neo4j.
    Translates raw Cypher records into serializable Python collections.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> None:
        """Initializes database credentials and driver config."""
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = username or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password123")
        self.driver: Optional[Driver] = None

    def connect(self) -> None:
        """Establishes connection to the Neo4j database."""
        try:
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password)
            )
            self.driver.verify_connectivity()
            logger.info("Query Engine successfully connected to Neo4j.")
        except Exception as e:
            logger.error(f"Query Engine connection failed: {e}")
            raise ConnectionError(f"Failed to connect to Neo4j query endpoint: {e}") from e

    def close(self) -> None:
        """Closes the database driver connection."""
        if self.driver:
            self.driver.close()
            self.driver = None

    def __enter__(self) -> "GraphQueryEngine":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _execute_read_query(self, cypher: str, parameters: Optional[Dict[str, Any]] = None) -> List[Record]:
        """Helper to run a Cypher read query in a transaction session."""
        if not self.driver:
            raise ConnectionError("Query Engine driver is not connected. Use connect() first.")
        
        try:
            with self.driver.session() as session:
                result = session.run(cypher, parameters or {})
                return list(result)
        except Exception as e:
            logger.error(f"Cypher execution failed: {cypher} with params {parameters}. Error: {e}")
            raise

    def find_direct_callers(self, function_id: str) -> List[Dict[str, Any]]:
        """
        Finds all functions that call the target function directly.
        Returns call line number and call type.
        """
        cypher = """
        MATCH (caller:Function)-[r:CALLS]->(target:Function {id: $func_id})
        RETURN caller.id AS id, 
               caller.name AS name, 
               caller.file AS file, 
               caller.start_line AS start_line, 
               caller.end_line AS end_line,
               r.line_number AS line_number, 
               r.call_type AS call_type
        ORDER BY caller.id, r.line_number
        """
        records = self._execute_read_query(cypher, {"func_id": function_id})
        return [dict(rec) for rec in records]

    def find_recursive_callers(self, function_id: str, max_depth: int = 5) -> List[Dict[str, Any]]:
        """
        Finds all functions that call the target function recursively up to a max depth.
        """
        # Validate depth to avoid Cypher injection or stack overflows
        depth_str = f"1..{int(max_depth)}"
        cypher = f"""
        MATCH (caller:Function)-[:CALLS*{depth_str}]->(target:Function {{id: $func_id}})
        WHERE caller.id <> $func_id
        RETURN DISTINCT caller.id AS id, 
                        caller.name AS name, 
                        caller.file AS file, 
                        caller.start_line AS start_line, 
                        caller.end_line AS end_line
        ORDER BY caller.id
        """
        records = self._execute_read_query(cypher, {"func_id": function_id})
        return [dict(rec) for rec in records]

    def find_direct_callees(self, function_id: str) -> List[Dict[str, Any]]:
        """
        Finds all functions called by the target function directly.
        """
        cypher = """
        MATCH (source:Function {id: $func_id})-[r:CALLS]->(callee:Function)
        RETURN callee.id AS id, 
               callee.name AS name, 
               callee.file AS file, 
               callee.start_line AS start_line, 
               callee.end_line AS end_line,
               r.line_number AS line_number, 
               r.call_type AS call_type
        ORDER BY callee.id, r.line_number
        """
        records = self._execute_read_query(cypher, {"func_id": function_id})
        return [dict(rec) for rec in records]

    def find_recursive_callees(self, function_id: str, max_depth: int = 5) -> List[Dict[str, Any]]:
        """
        Finds all functions that the target function calls recursively.
        """
        depth_str = f"1..{int(max_depth)}"
        cypher = f"""
        MATCH (source:Function {{id: $func_id}})-[:CALLS*{depth_str}]->(callee:Function)
        WHERE callee.id <> $func_id
        RETURN DISTINCT callee.id AS id, 
                        callee.name AS name, 
                        callee.file AS file, 
                        callee.start_line AS start_line, 
                        callee.end_line AS end_line
        ORDER BY callee.id
        """
        records = self._execute_read_query(cypher, {"func_id": function_id})
        return [dict(rec) for rec in records]

    def find_functions_in_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Retrieves all functions defined in the given file.
        """
        cypher = """
        MATCH (f:File {path: $file_path})-[:DEFINES]->(fn:Function)
        RETURN fn.id AS id, 
               fn.name AS name, 
               fn.file AS file, 
               fn.start_line AS start_line, 
               fn.end_line AS end_line
        ORDER BY fn.start_line
        """
        records = self._execute_read_query(cypher, {"file_path": file_path})
        return [dict(rec) for rec in records]

    def find_classes_in_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Retrieves all classes defined in the given file.
        """
        cypher = """
        MATCH (f:File {path: $file_path})-[:DEFINES]->(c:Class)
        RETURN c.id AS id, 
               c.name AS name, 
               c.file AS file
        ORDER BY c.name
        """
        records = self._execute_read_query(cypher, {"file_path": file_path})
        return [dict(rec) for rec in records]

    def find_methods_in_class(self, class_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves all methods (functions) belonging to the given class.
        """
        cypher = """
        MATCH (c:Class {id: $class_id})-[:HAS_METHOD]->(fn:Function)
        RETURN fn.id AS id, 
               fn.name AS name, 
               fn.file AS file, 
               fn.start_line AS start_line, 
               fn.end_line AS end_line
        ORDER BY fn.start_line
        """
        records = self._execute_read_query(cypher, {"class_id": class_id})
        return [dict(rec) for rec in records]

    def find_impacted_functions(self, function_id: str, max_depth: int = 5) -> List[Dict[str, Any]]:
        """
        Finds all functions affected by changes in the given function.
        Alias for find_recursive_callers since callers depend on this function.
        """
        return self.find_recursive_callers(function_id, max_depth)

    def find_impacted_entities(self, function_id: str, max_depth: int = 5) -> Dict[str, Any]:
        """
        Answers: 'If this function changes, what functions, classes, and files are affected?'
        Returns a dictionary grouping impacted functions, classes, and files.
        """
        # 1. Affected functions (including target itself)
        affected_funcs = self.find_recursive_callers(function_id, max_depth)
        
        # Include target function in the impacted list as it is the node being changed
        target_records = self._execute_read_query(
            "MATCH (fn:Function {id: $func_id}) RETURN fn.id AS id, fn.name AS name, fn.file AS file, fn.start_line AS start_line, fn.end_line AS end_line",
            {"func_id": function_id}
        )
        target_list = [dict(rec) for rec in target_records]
        
        all_funcs = target_list + affected_funcs
        func_ids = [fn["id"] for fn in all_funcs]

        # 2. Affected classes (classes containing affected functions as methods)
        classes_cypher = """
        MATCH (c:Class)-[:HAS_METHOD]->(fn:Function)
        WHERE fn.id IN $func_ids
        RETURN DISTINCT c.id AS id, c.name AS name, c.file AS file
        """
        classes_records = self._execute_read_query(classes_cypher, {"func_ids": func_ids})
        affected_classes = [dict(rec) for rec in classes_records]

        # 3. Affected files (files defining affected functions OR defining affected classes)
        # Also include any files importing the defining files, since they import modified items
        files_cypher = """
        MATCH (f:File)-[:DEFINES]->(node)
        WHERE (node:Function AND node.id IN $func_ids) OR (node:Class AND node.id IN $class_ids)
        WITH DISTINCT f
        OPTIONAL MATCH (importer:File)-[:IMPORTS]->(f)
        RETURN DISTINCT coalesce(importer.path, f.path) AS path, 
                        coalesce(importer.language, f.language) AS language
        """
        class_ids = [c["id"] for c in affected_classes]
        files_records = self._execute_read_query(files_cypher, {"func_ids": func_ids, "class_ids": class_ids})
        affected_files = [dict(rec) for rec in files_records]

        return {
            "target_function": target_list[0] if target_list else None,
            "functions": all_funcs,
            "classes": affected_classes,
            "files": affected_files
        }

    def get_dependency_statistics(self) -> Dict[str, int]:
        """
        Calculates count overview for Files, Functions, Classes, Imports and Calls.
        """
        cypher = """
        CALL { MATCH (f:File) RETURN count(f) AS file_count }
        CALL { MATCH (fn:Function) RETURN count(fn) AS function_count }
        CALL { MATCH (c:Class) RETURN count(c) AS class_count }
        CALL { MATCH ()-[r:IMPORTS]->() RETURN count(r) AS import_count }
        CALL { MATCH ()-[r:CALLS]->() RETURN count(r) AS call_count }
        RETURN file_count, function_count, class_count, import_count, call_count
        """
        records = self._execute_read_query(cypher)
        if records:
            return dict(records[0])
        return {
            "file_count": 0,
            "function_count": 0,
            "class_count": 0,
            "import_count": 0,
            "call_count": 0
        }

    def get_graph_statistics(self) -> Dict[str, Any]:
        """
        Calculates advanced topology statistics on the call graph:
        - Root functions (in-degree = 0, entrypoints)
        - Leaf functions (out-degree = 0, terminal)
        - Calls by type (direct, method, etc.)
        - Average out-degree (average direct calls per function)
        """
        # Root functions
        roots_query = """
        MATCH (fn:Function)
        WHERE NOT ()-[:CALLS]->(fn)
        RETURN count(fn) AS count
        """
        roots = self._execute_read_query(roots_query)[0]["count"]

        # Leaf functions
        leaves_query = """
        MATCH (fn:Function)
        WHERE NOT (fn)-[:CALLS]->()
        RETURN count(fn) AS count
        """
        leaves = self._execute_read_query(leaves_query)[0]["count"]

        # Call types distribution
        types_query = """
        MATCH ()-[r:CALLS]->()
        RETURN r.call_type AS call_type, count(r) AS count
        ORDER BY count DESC
        """
        types_records = self._execute_read_query(types_query)
        call_types = {rec["call_type"]: rec["count"] for rec in types_records}

        # Average out degree
        avg_degree_query = """
        MATCH (fn:Function)
        OPTIONAL MATCH (fn)-[r:CALLS]->()
        WITH fn, count(r) AS degree
        RETURN avg(degree) AS avg_out_degree
        """
        avg_degree_rec = self._execute_read_query(avg_degree_query)
        avg_out_degree = avg_degree_rec[0]["avg_out_degree"] or 0.0

        return {
            "root_functions_count": roots,
            "leaf_functions_count": leaves,
            "average_out_degree": round(avg_out_degree, 2),
            "call_types_distribution": call_types
        }

    def export_impact_tree(self, function_id: str, max_depth: int = 5) -> Dict[str, Any]:
        """
        Builds a nested impact tree indicating why a function is affected.
        Traverses callers starting from the target function.
        """
        # Fetch target function details
        target_info = self._execute_read_query(
            "MATCH (fn:Function {id: $func_id}) RETURN fn",
            {"func_id": function_id}
        )
        if not target_info:
            return {}

        target_node = dict(target_info[0]["fn"])

        # Fetch paths from any caller up to max depth ending at target function
        depth_str = f"1..{int(max_depth)}"
        cypher_paths = f"""
        MATCH path = (caller:Function)-[:CALLS*{depth_str}]->(target:Function {{id: $func_id}})
        RETURN path
        """
        records = self._execute_read_query(cypher_paths, {"func_id": function_id})

        # Build node registry & adjacency map
        nodes: Dict[str, Dict[str, Any]] = {
            target_node["id"]: {
                "id": target_node["id"],
                "name": target_node["name"],
                "file": target_node["file"],
                "start_line": target_node["start_line"],
                "end_line": target_node["end_line"],
                "impacted_by": []
            }
        }

        # We construct relationship records: source -> target
        relationships: List[Dict[str, Any]] = []

        for record in records:
            path = record["path"]
            # Traverse path segments
            for rel in path.relationships:
                start_node = rel.start_node
                end_node = rel.end_node
                
                start_id = start_node["id"]
                end_id = end_node["id"]

                # Register nodes
                for node in [start_node, end_node]:
                    nid = node["id"]
                    if nid not in nodes:
                        nodes[nid] = {
                            "id": nid,
                            "name": node["name"],
                            "file": node["file"],
                            "start_line": node["start_line"],
                            "end_line": node["end_line"],
                            "impacted_by": []
                        }

                # Register relationship if not duplicate
                rel_info = {
                    "caller_id": start_id,
                    "callee_id": end_id,
                    "line_number": rel["line_number"],
                    "call_type": rel["call_type"]
                }
                if rel_info not in relationships:
                    relationships.append(rel_info)

        # Build recursive tree starting from target_id by matching relationships
        # Note: We want to show "impacted_by", so target is called by callers.
        # Thus: callee is target, caller is the children in the "impacted_by" array.
        
        # Helper to recursively build tree branch
        visited = set()

        def build_branch(node_id: str) -> Dict[str, Any]:
            if node_id in visited:
                # Handle recursive calls/loops gracefully
                return {
                    "id": node_id,
                    "name": nodes[node_id]["name"],
                    "file": nodes[node_id]["file"],
                    "note": "Cycle detected"
                }
            
            visited.add(node_id)
            
            node_data = {
                "id": node_id,
                "name": nodes[node_id]["name"],
                "file": nodes[node_id]["file"],
                "start_line": nodes[node_id]["start_line"],
                "end_line": nodes[node_id]["end_line"],
                "impacted_by": []
            }

            # Find all relationships where the current node is the CALLEE
            for rel in relationships:
                if rel["callee_id"] == node_id:
                    caller_id = rel["caller_id"]
                    branch = build_branch(caller_id)
                    branch["call_line"] = rel["line_number"]
                    branch["call_type"] = rel["call_type"]
                    node_data["impacted_by"].append(branch)

            visited.remove(node_id)
            return node_data

        return build_branch(target_node["id"])

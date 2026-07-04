"""
Ingestion module for importing extracted dependency metadata into Neo4j.
Handles environment loading, JSON validation, database connection, and transactional MERGE updates.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from jsonschema import validate, ValidationError
from neo4j import GraphDatabase, Driver, Session, Transaction

# Load environment variables from .env
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("graph_engine.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("graph_ingest")


# JSON schema definition matching the project data contract
JSON_CONTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "language": {"type": "string"},
                    "imports": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["path", "language"]
            }
        },
        "functions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "file": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "line_number": {"type": "integer"},
                                "call_type": {
                                    "type": "string",
                                    "enum": ["direct", "method", "library", "recursive", "async"]
                                }
                            },
                            "required": ["id", "line_number", "call_type"]
                        }
                    }
                },
                "required": ["id", "name", "file", "start_line", "end_line"]
            }
        },
        "classes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "file": {"type": "string"},
                    "methods": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["id", "name", "file"]
            }
        }
    },
    "required": ["files", "functions", "classes"]
}


class GraphEngineError(Exception):
    """Base exception for all Graph Engine errors."""
    pass


class DatabaseConnectionError(GraphEngineError):
    """Raised when connecting to Neo4j database fails."""
    pass


class InvalidJSONError(GraphEngineError):
    """Raised when JSON validation fails against the schema contract."""
    pass


class Neo4jIngestor:
    """
    Ingestor class to connect to a Neo4j instance and import repository metadata.
    Enforces constraints, handles transaction retries, and supports database cleaning.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None
    ) -> None:
        """
        Initializes connection using provided credentials, or loads them from environment.
        """
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.username = username or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password123")
        self.driver: Optional[Driver] = None

    def connect(self) -> None:
        """
        Establishes driver connection to Neo4j. Raises DatabaseConnectionError if connection fails.
        """
        try:
            logger.info(f"Connecting to Neo4j database at {self.uri} as user {self.username}...")
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password)
            )
            # Verify connectivity
            self.driver.verify_connectivity()
            logger.info("Successfully connected to Neo4j database.")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j database: {e}", exc_info=True)
            raise DatabaseConnectionError(f"Neo4j database connection failed: {e}") from e

    def close(self) -> None:
        """Closes the active database driver connection."""
        if self.driver:
            logger.info("Closing Neo4j database connection...")
            self.driver.close()
            self.driver = None

    def __enter__(self) -> "Neo4jIngestor":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def validate_json_data(self, data: Dict[str, Any]) -> None:
        """
        Validates input JSON data against the JSON_CONTRACT_SCHEMA.
        Raises InvalidJSONError if validation fails.
        """
        try:
            logger.info("Validating ingestion payload against schema contract...")
            validate(instance=data, schema=JSON_CONTRACT_SCHEMA)
            logger.info("JSON payload validation succeeded.")
        except ValidationError as e:
            logger.error(f"JSON validation failed: {e.message} at path {list(e.path)}")
            raise InvalidJSONError(f"JSON schema validation failed: {e.message}") from e

    def apply_constraints(self, session: Session) -> None:
        """
        Reads constraints.cypher (if available) or applies constraints directly
        to enforce uniqueness indices on File(path), Function(id), and Class(id).
        """
        logger.info("Applying Neo4j constraints...")
        
        # Load constraints cypher file path
        script_dir = os.path.dirname(os.path.abspath(__file__))
        constraints_file = os.path.join(script_dir, "constraints.cypher")
        
        statements: List[str] = []
        if os.path.exists(constraints_file):
            try:
                with open(constraints_file, "r", encoding="utf-8") as f:
                    raw_content = f.read()
                    # Filter out comment lines before splitting by semicolon
                    lines = []
                    for line in raw_content.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("//") or stripped.startswith("#"):
                            continue
                        lines.append(line)
                    cleaned_content = "\n".join(lines)
                    statements = [
                        stmt.strip() for stmt in cleaned_content.split(";")
                        if stmt.strip()
                    ]
            except Exception as e:
                logger.warning(f"Could not read constraints file: {e}. Falling back to default statements.")
        
        if not statements:
            statements = [
                "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
                "CREATE CONSTRAINT function_id_unique IF NOT EXISTS FOR (fn:Function) REQUIRE fn.id IS UNIQUE",
                "CREATE CONSTRAINT class_id_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.id IS UNIQUE"
            ]

        def _apply_tx(tx: Transaction) -> None:
            for statement in statements:
                logger.debug(f"Executing Cypher constraint: {statement}")
                tx.run(statement)

        try:
            session.execute_write(_apply_tx)
            logger.info("Uniqueness constraints configured successfully.")
        except Exception as e:
            logger.error(f"Failed to apply database constraints: {e}")
            raise GraphEngineError(f"Database constraint configuration failed: {e}") from e

    def ingest(self, data: Dict[str, Any], clear_existing: bool = False) -> None:
        """
        Performs full transactional database ingestion of the validated metadata schema.
        
        Args:
            data: The JSON contract metadata dictionary.
            clear_existing: If True, executes DETACH DELETE on the whole graph before ingesting.
        """
        # Validate data structure before starting transaction
        self.validate_json_data(data)

        if not self.driver:
            raise DatabaseConnectionError("Driver not initialized. Please connect() first.")

        try:
            with self.driver.session() as session:
                # 1. Clear database if requested
                if clear_existing:
                    logger.warning("Clearing all existing nodes and relationships from Neo4j...")
                    session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
                    logger.info("Database cleared.")

                # 2. Ensure constraints exist
                self.apply_constraints(session)

                # 3. Create Files
                files = data.get("files", [])
                if files:
                    logger.info(f"Ingesting {len(files)} File nodes...")
                    session.execute_write(self._create_files_tx, files)

                # 4. Create Functions
                functions = data.get("functions", [])
                if functions:
                    logger.info(f"Ingesting {len(functions)} Function nodes...")
                    session.execute_write(self._create_functions_tx, functions)

                # 5. Create Classes
                classes = data.get("classes", [])
                if classes:
                    logger.info(f"Ingesting {len(classes)} Class nodes...")
                    session.execute_write(self._create_classes_tx, classes)

                # 6. Create IMPORTS relationships
                if files:
                    logger.info("Ingesting IMPORTS relationships...")
                    session.execute_write(self._create_imports_tx, files)

                # 7. Create DEFINES relationships
                if files or functions or classes:
                    logger.info("Ingesting DEFINES relationships...")
                    session.execute_write(self._create_defines_tx, data)

                # 8. Create HAS_METHOD relationships
                if classes:
                    logger.info("Ingesting HAS_METHOD relationships...")
                    session.execute_write(self._create_has_method_tx, classes)

                # 9. Create CALLS relationships
                if functions:
                    logger.info("Ingesting CALLS relationships...")
                    session.execute_write(self._create_calls_tx, functions)

            logger.info("Ingestion transaction committed successfully.")

        except Exception as e:
            logger.critical(f"Database ingestion transaction failed: {e}", exc_info=True)
            raise GraphEngineError(f"Data ingestion failed: {e}") from e

    # --- Transaction Helper Methods ---

    @staticmethod
    def _create_files_tx(tx: Transaction, files: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge File nodes."""
        query = """
        UNWIND $files AS file_data
        MERGE (f:File {path: file_data.path})
        ON CREATE SET f.language = file_data.language
        ON MATCH SET f.language = file_data.language
        """
        tx.run(query, files=files)

    @staticmethod
    def _create_functions_tx(tx: Transaction, functions: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge Function nodes."""
        query = """
        UNWIND $functions AS fn_data
        MERGE (fn:Function {id: fn_data.id})
        ON CREATE SET 
            fn.name = fn_data.name,
            fn.file = fn_data.file,
            fn.start_line = fn_data.start_line,
            fn.end_line = fn_data.end_line
        ON MATCH SET 
            fn.name = fn_data.name,
            fn.file = fn_data.file,
            fn.start_line = fn_data.start_line,
            fn.end_line = fn_data.end_line
        """
        tx.run(query, functions=functions)

    @staticmethod
    def _create_classes_tx(tx: Transaction, classes: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge Class nodes."""
        query = """
        UNWIND $classes AS class_data
        MERGE (c:Class {id: class_data.id})
        ON CREATE SET 
            c.name = class_data.name,
            c.file = class_data.file
        ON MATCH SET 
            c.name = class_data.name,
            c.file = class_data.file
        """
        tx.run(query, classes=classes)

    @staticmethod
    def _create_imports_tx(tx: Transaction, files: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge IMPORTS relations between Files."""
        query = """
        UNWIND $files AS file_data
        MATCH (f1:File {path: file_data.path})
        WITH f1, file_data.imports AS imports
        UNWIND imports AS imp_path
        MERGE (f2:File {path: imp_path})
        MERGE (f1)-[:IMPORTS]->(f2)
        """
        tx.run(query, files=files)

    @staticmethod
    def _create_defines_tx(tx: Transaction, data: Dict[str, Any]) -> None:
        """Transaction callback to create DEFINES relations from File to Function and Class."""
        funcs = data.get("functions", [])
        if funcs:
            funcs_query = """
            UNWIND $functions AS fn_data
            MATCH (f:File {path: fn_data.file})
            MATCH (fn:Function {id: fn_data.id})
            MERGE (f)-[:DEFINES]->(fn)
            """
            tx.run(funcs_query, functions=funcs)

        classes = data.get("classes", [])
        if classes:
            classes_query = """
            UNWIND $classes AS class_data
            MATCH (f:File {path: class_data.file})
            MATCH (c:Class {id: class_data.id})
            MERGE (f)-[:DEFINES]->(c)
            """
            tx.run(classes_query, classes=classes)

    @staticmethod
    def _create_has_method_tx(tx: Transaction, classes: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge HAS_METHOD relations from Class to Function."""
        query = """
        UNWIND $classes AS class_data
        MATCH (c:Class {id: class_data.id})
        WITH c, class_data.methods AS methods
        UNWIND methods AS method_id
        MATCH (fn:Function {id: method_id})
        MERGE (c)-[:HAS_METHOD]->(fn)
        """
        tx.run(query, classes=classes)

    @staticmethod
    def _create_calls_tx(tx: Transaction, functions: List[Dict[str, Any]]) -> None:
        """Transaction callback to merge CALLS relations between Functions."""
        query = """
        UNWIND $functions AS fn_data
        MATCH (caller:Function {id: fn_data.id})
        WITH caller, fn_data.calls AS calls
        UNWIND calls AS call_data
        MERGE (callee:Function {id: call_data.id})
        MERGE (caller)-[r:CALLS {line_number: call_data.line_number}]->(callee)
        ON CREATE SET r.call_type = call_data.call_type
        ON MATCH SET r.call_type = call_data.call_type
        """
        tx.run(query, functions=functions)


if __name__ == "__main__":
    # Test script loading sample json directly if executed
    import sys
    if len(sys.argv) > 1:
        json_file_path = sys.argv[1]
        try:
            with open(json_file_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            ingestor = Neo4jIngestor()
            with ingestor:
                ingestor.ingest(payload, clear_existing=True)
            print("Successfully ingested sample JSON.")
        except Exception as err:
            print(f"Ingestion test error: {err}")
            sys.exit(1)
    else:
        print("Usage: python ingest.py <path_to_json>")

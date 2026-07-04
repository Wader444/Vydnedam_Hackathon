"""
Unit tests for the Vydnedam_Hackathon Graph Engine.
Uses unittest.mock to mock Neo4j driver interactions, allowing offline/independent test runs.
"""

import unittest
from unittest.mock import MagicMock, patch
import jsonschema

from graph.ingest import (
    Neo4jIngestor,
    InvalidJSONError,
    DatabaseConnectionError,
    GraphEngineError
)


class TestNeo4jIngestor(unittest.TestCase):
    """Test suite covering ingestion validation and Neo4j driver interactions."""

    def setUp(self) -> None:
        self.valid_data = {
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
                    "methods": ["auth.py::authenticate_user"]
                }
            ]
        }

    def test_validation_valid_json(self) -> None:
        """Verifies that a correct payload structure passes validation without exceptions."""
        ingestor = Neo4jIngestor(uri="bolt://localhost:7687", username="neo4j", password="pwd")
        try:
            ingestor.validate_json_data(self.valid_data)
        except InvalidJSONError as e:
            self.fail(f"validate_json_data failed on a valid schema: {e}")

    def test_validation_missing_required_keys(self) -> None:
        """Verifies validation failure when required root components are missing."""
        ingestor = Neo4jIngestor()
        invalid_data = {
            "files": [],
            "functions": []
            # 'classes' is missing
        }
        with self.assertRaises(InvalidJSONError):
            ingestor.validate_json_data(invalid_data)

    def test_validation_invalid_function_call_type(self) -> None:
        """Verifies validation failure if function calls contain an invalid call_type."""
        ingestor = Neo4jIngestor()
        bad_call_type_data = {
            "files": [],
            "classes": [],
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
                            "call_type": "invalid_type_here"  # Not in enum
                        }
                    ]
                }
            ]
        }
        with self.assertRaises(InvalidJSONError):
            ingestor.validate_json_data(bad_call_type_data)

    @patch("graph.ingest.GraphDatabase")
    def test_connect_success(self, mock_graph_db: MagicMock) -> None:
        """Verifies successful database driver connection and verification."""
        mock_driver = MagicMock()
        mock_graph_db.driver.return_value = mock_driver

        ingestor = Neo4jIngestor("bolt://test:7687", "user", "pass")
        ingestor.connect()

        mock_graph_db.driver.assert_called_once_with("bolt://test:7687", auth=("user", "pass"))
        mock_driver.verify_connectivity.assert_called_once()
        self.assertEqual(ingestor.driver, mock_driver)

    @patch("graph.ingest.GraphDatabase")
    def test_connect_failure(self, mock_graph_db: MagicMock) -> None:
        """Verifies exception handling when the Neo4j driver connection fails."""
        mock_graph_db.driver.side_effect = Exception("Connection Refused")

        ingestor = Neo4jIngestor()
        with self.assertRaises(DatabaseConnectionError):
            ingestor.connect()

    @patch("graph.ingest.GraphDatabase")
    def test_ingestion_transaction_flow(self, mock_graph_db: MagicMock) -> None:
        """
        Tests transaction orchestration during ingestion: session opening, clearing,
        constraint configuration, and batch uploads.
        """
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_transaction = MagicMock()
        
        # Configure mocks to return transactions
        mock_graph_db.driver.return_value = mock_driver
        mock_driver.session.return_value = mock_session
        mock_session.__enter__.return_value = mock_session
        
        # Mock session.execute_write behavior to execute the callback synchronously
        def mock_execute_write(callback, *args, **kwargs):
            return callback(mock_transaction, *args, **kwargs)
        mock_session.execute_write.side_effect = mock_execute_write

        ingestor = Neo4jIngestor("bolt://test:7687", "user", "pass")
        ingestor.driver = mock_driver

        # Execute ingest with database clearing turned on
        ingestor.ingest(self.valid_data, clear_existing=True)

        # Asserts
        # Must have run a clear query: MATCH (n) DETACH DELETE n
        mock_transaction.run.assert_any_call("MATCH (n) DETACH DELETE n")

        # Must have applied constraints
        # Uniqueness constraint for File path is expected
        mock_transaction.run.assert_any_call(
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS\nFOR (f:File)\nREQUIRE f.path IS UNIQUE"
        )

        # Must check calls to insert File nodes, Function nodes, Class nodes
        # Verify call arguments structure
        any_file_call = False
        any_func_call = False
        any_class_call = False
        for call_args in mock_transaction.run.call_args_list:
            query = call_args[0][0]
            if "UNWIND $files AS file_data" in query:
                any_file_call = True
            elif "UNWIND $functions AS fn_data" in query:
                any_func_call = True
            elif "UNWIND $classes AS class_data" in query:
                any_class_call = True

        self.assertTrue(any_file_call, "Files insert transaction query was not run.")
        self.assertTrue(any_func_call, "Functions insert transaction query was not run.")
        self.assertTrue(any_class_call, "Classes insert transaction query was not run.")


if __name__ == "__main__":
    unittest.main()

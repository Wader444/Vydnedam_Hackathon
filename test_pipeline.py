import os
import sys
from parser import parse_directory
from graph.ingest import Neo4jIngestor, InvalidJSONError

def find_file_for_callee(callee_name, parsed_functions):
    for f in parsed_functions:
        if f["name"] == callee_name:
            return f["file"]
    return "unknown.py"

def main():
    print("Parsing current directory...")
    parsed_functions = parse_directory(".")
    print(f"Extracted {len(parsed_functions)} functions.")

    # Map the parsed functions list into the payload schema required by Neo4jIngestor
    unique_files = set()
    files_list = []
    for func in parsed_functions:
        file_path = func["file"]
        rel_path = os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
        if rel_path not in unique_files:
            unique_files.add(rel_path)
            files_list.append({
                "path": rel_path,
                "language": "python",
                "imports": []
            })

    functions_list = []
    for func in parsed_functions:
        file_path = func["file"]
        rel_path = os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
        
        calls_list = []
        for callee in func["calls"]:
            callee_file = find_file_for_callee(callee, parsed_functions)
            callee_rel_path = os.path.relpath(callee_file, os.getcwd()).replace("\\", "/")
            calls_list.append({
                "id": f"{callee_rel_path}::{callee}",
                "line_number": func["start_line"],  # placeholder line number
                "call_type": "direct"
            })

        functions_list.append({
            "id": f"{rel_path}::{func['name']}",
            "name": func["name"],
            "file": rel_path,
            "start_line": func["start_line"],
            "end_line": func["end_line"],
            "calls": calls_list
        })

    payload = {
        "files": files_list,
        "functions": functions_list,
        "classes": []  # satisfied schema requirement
    }

    print("Validating generated payload against database ingestion schema...")
    ingestor = Neo4jIngestor()
    try:
        ingestor.validate_json_data(payload)
        print("SUCCESS: JSON schema validation completed without throwing InvalidJSONError.")
    except InvalidJSONError as e:
        print(f"FAILURE: InvalidJSONError was raised: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

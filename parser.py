import json
import os
import sys
import tree_sitter
import tree_sitter_python

def parse_file(file_path: str) -> list:
    """
    Parses a python source file using tree-sitter, extracting top-level function definitions
    and the names of the functions called directly inside their bodies.
    """
    # Load and parse the source file
    with open(file_path, 'rb') as f:
        source_code = f.read()

    language = tree_sitter.Language(tree_sitter_python.language())
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source_code)
    root_node = tree.root_node

    # Query to extract top-level functions (immediate children of module)
    top_level_query_str = """
    (module
      (function_definition
        name: (identifier) @function.name) @function.def)
    """
    top_level_query = tree_sitter.Query(language, top_level_query_str)
    top_level_cursor = tree_sitter.QueryCursor(top_level_query)

    # Query to extract call expressions
    call_query_str = """
    (call
      function: [
        (identifier) @call.name
        (attribute attribute: (identifier) @call.name)
      ])
    """
    call_query = tree_sitter.Query(language, call_query_str)
    call_cursor = tree_sitter.QueryCursor(call_query)

    results = []

    # Run top-level query on the root node
    for _, capture_dict in top_level_cursor.matches(root_node):
        func_node = capture_dict['function.def'][0]
        func_name_node = capture_dict['function.name'][0]
        func_name = source_code[func_name_node.start_byte:func_name_node.end_byte].decode('utf-8')

        # Find calls within the function's body
        calls = []
        for _, call_captures in call_cursor.matches(func_node):
            call_name_node = call_captures['call.name'][0]
            call_name = source_code[call_name_node.start_byte:call_name_node.end_byte].decode('utf-8')

            # Parent traversal to ensure direct calls only (not nested inside inner function definitions)
            is_direct = True
            parent = call_name_node.parent
            while parent and parent != func_node:
                if parent.type == 'function_definition':
                    is_direct = False
                    break
                parent = parent.parent

            if is_direct:
                if call_name not in calls:
                    calls.append(call_name)

        start_line = func_node.start_point[0] + 1
        end_line = func_node.end_point[0] + 1

        results.append({
            "name": func_name,
            "file": os.path.abspath(file_path),
            "start_line": start_line,
            "end_line": end_line,
            "calls": calls
        })

    return results

def filter_external_calls(parsed_data: list) -> list:
    """
    Filters the 'calls' list for each function. A function name remains in
    the 'calls' list only if it is defined as a custom function in the parsed data.
    """
    defined_names = {func['name'] for func in parsed_data}
    for func in parsed_data:
        func['calls'] = [call for call in func['calls'] if call in defined_names]
    return parsed_data

def parse_directory(directory_path: str) -> list:
    """
    Recursively scans all `.py` files inside the target directory,
    parses each one, and returns a single combined list of dictionaries
    filtered to include only calls to custom functions defined within the directory.
    """
    all_results = []
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.endswith('.py'):
                full_path = os.path.join(root, file)
                try:
                    all_results.extend(parse_file(full_path))
                except Exception as e:
                    print(f"Warning: Failed to parse '{full_path}': {e}", file=sys.stderr)
    return filter_external_calls(all_results)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python parser.py <path_to_target_directory>", file=sys.stderr)
        sys.exit(1)
        
    target_path = sys.argv[1]
    if not os.path.exists(target_path):
        print(f"Error: Path '{target_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    if os.path.isdir(target_path):
        parsed_data = parse_directory(target_path)
    else:
        parsed_data = filter_external_calls(parse_file(target_path))
        
    print(json.dumps(parsed_data, indent=2))

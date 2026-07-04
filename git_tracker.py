import subprocess
import re
import os
import sys
import tree_sitter
import tree_sitter_python
from shared.path_utils import normalize_path
from parser import REPO_ROOT

def get_git_repo_root() -> str:
    """
    Runs git command to retrieve the top-level repository directory.
    """
    try:
        result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error: Not in a git repository or git is not installed. {e}", file=sys.stderr)
        sys.exit(1)

def get_git_diff() -> str:
    """
    Runs git diff to get unstaged and staged changes.
    """
    # Run git diff (staged and unstaged)
    # We use HEAD to track all local modifications (staged and unstaged changes) compared to the last commit.
    try:
        result = subprocess.run(['git', 'diff', 'HEAD'], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error executing git diff: {e}", file=sys.stderr)
        sys.exit(1)

def parse_git_diff(diff_text: str) -> dict:
    """
    Parses the git diff text line-by-line to extract modified line numbers for each file.
    Returns a dictionary mapping file path (relative to repo root) to a set of modified/added line numbers.
    """
    modified_lines_by_file = {}
    
    file_target_re = re.compile(r'^\+\+\+\s+b/(.*)$')
    hunk_header_re = re.compile(r'^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@')
    
    current_file = None
    current_line = 0
    in_hunk = False
    
    for line in diff_text.splitlines():
        # Check for file target header
        file_match = file_target_re.match(line)
        if file_match:
            current_file = file_match.group(1)
            # Only track python files
            if current_file.endswith('.py'):
                modified_lines_by_file[current_file] = set()
            else:
                current_file = None
            in_hunk = False
            continue
            
        if current_file is None:
            continue
            
        # Check for hunk header
        hunk_match = hunk_header_re.match(line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            in_hunk = True
            continue
            
        if in_hunk:
            if line.startswith('+') and not line.startswith('+++'):
                modified_lines_by_file[current_file].add(current_line)
                current_line += 1
            elif line.startswith('-') and not line.startswith('---'):
                # Line was deleted; does not exist in the new file state
                pass
            else:
                # Context line (starts with space or empty)
                current_line += 1
                
    return modified_lines_by_file

def get_function_line_ranges(file_path: str, language: tree_sitter.Language) -> list:
    """
    Parses the file using tree-sitter to find top-level function names and their line ranges (1-indexed).
    """
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'rb') as f:
        source_code = f.read()
    
    parser = tree_sitter.Parser(language)
    tree = parser.parse(source_code)
    root_node = tree.root_node
    
    top_level_query_str = """
    (module
      (function_definition
        name: (identifier) @function.name) @function.def)
    """
    top_level_query = tree_sitter.Query(language, top_level_query_str)
    top_level_cursor = tree_sitter.QueryCursor(top_level_query)
    
    ranges = []
    for _, capture_dict in top_level_cursor.matches(root_node):
        func_node = capture_dict['function.def'][0]
        func_name_node = capture_dict['function.name'][0]
        func_name = source_code[func_name_node.start_byte:func_name_node.end_byte].decode('utf-8')
        
        # start_point is (row, column) where row is 0-indexed. Convert to 1-indexed.
        start_line = func_node.start_point[0] + 1
        end_line = func_node.end_point[0] + 1
        
        ranges.append({
            "name": func_name,
            "start": start_line,
            "end": end_line
        })
    return ranges

def get_modified_functions() -> list:
    """
    Determines which top-level functions have been modified or added in the active git repository.
    """
    repo_root = get_git_repo_root()
    diff_text = get_git_diff()
    modified_lines = parse_git_diff(diff_text)
    
    language = tree_sitter.Language(tree_sitter_python.language())
    modified_functions = set()
    
    for rel_path, lines in modified_lines.items():
        if not lines:
            continue
        abs_path = os.path.join(repo_root, rel_path)
        function_ranges = get_function_line_ranges(abs_path, language)
        
        for func in function_ranges:
            # Check if any modified/added line number falls inside the function definition range
            if any(func['start'] <= line <= func['end'] for line in lines):
                modified_functions.add(func['name'])
                
    return sorted(list(modified_functions))

def get_modified_function_ids() -> list:
    """
    Determines which top-level functions have been modified or added in the active git repository,
    returning their standardized IDs format: '{relative_file_path}::{function_name}'.
    Uses forward slashes for relative file paths to ensure cross-platform database compatibility.
    """
    repo_root = get_git_repo_root()
    diff_text = get_git_diff()
    modified_lines = parse_git_diff(diff_text)
    
    language = tree_sitter.Language(tree_sitter_python.language())
    modified_ids = set()
    
    for rel_path, lines in modified_lines.items():
        if not lines:
            continue
        abs_path = os.path.join(repo_root, rel_path)
        function_ranges = get_function_line_ranges(abs_path, language)
        
        for func in function_ranges:
            # Check if any modified/added line number falls inside the function definition range
            if any(func['start'] <= line <= func['end'] for line in lines):
                db_file_path = normalize_path(abs_path, REPO_ROOT)
                modified_ids.add(f"{db_file_path}::{func['name']}")
                
    return sorted(list(modified_ids))

if __name__ == '__main__':
    modified_funcs = get_modified_functions()
    modified_ids = get_modified_function_ids()
    import json
    print("Modified functions:")
    print(json.dumps(modified_funcs, indent=2))
    print("Modified function IDs:")
    print(json.dumps(modified_ids, indent=2))

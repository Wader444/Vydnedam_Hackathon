import os
import subprocess
from tree_sitter import Language, Parser
import tree_sitter_python

# Initialize tree-sitter Python parser
py_language = Language(tree_sitter_python.language())
parser = Parser(py_language)

def extract_symbols(filepath: str) -> dict:
    """
    Parses a Python file and returns its defined classes, functions, and file path.
    Standardized dictionary format for graph databases.
    """
    if not os.path.exists(filepath):
        return {"path": filepath, "classes": [], "functions": [], "top_level_assignments": []}

    try:
        with open(filepath, "rb") as f:
            code = f.read()
    except Exception:
        return {"path": filepath, "classes": [], "functions": [], "top_level_assignments": []}

    tree = parser.parse(code)
    root = tree.root_node

    classes = []
    functions = []
    top_level_assignments = []

    def get_node_text(node):
        return node.text.decode('utf-8', errors='ignore')

    def is_top_level(node):
        curr = node.parent
        while curr:
            if curr.type in ('function_definition', 'class_definition'):
                return False
            curr = curr.parent
        return True

    def get_lhs_identifiers(node):
        identifiers = []
        if node.type == 'identifier':
            identifiers.append(get_node_text(node))
        for child in node.children:
            identifiers.extend(get_lhs_identifiers(child))
        return list(set(identifiers))

    def get_identifiers(node):
        identifiers = []
        if node.type == 'identifier':
            identifiers.append(get_node_text(node))
        for child in node.children:
            identifiers.extend(get_identifiers(child))
        return list(set(identifiers))

    def walk_tree(node, current_class=None):
        if node.type == 'assignment' and is_top_level(node):
            lhs = node.children[0]
            vars_assigned = get_lhs_identifiers(lhs)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            top_level_assignments.append({
                "variables": vars_assigned,
                "start_line": start_line,
                "end_line": end_line
            })

        if node.type == 'class_definition':
            class_name = None
            for child in node.children:
                if child.type == 'identifier':
                    class_name = get_node_text(child)
                    break
            
            if class_name:
                classes.append({
                    "name": class_name,
                    "file": filepath
                })
                
            # Traverse class body
            for child in node.children:
                walk_tree(child, current_class=class_name)

        elif node.type == 'function_definition':
            func_name = None
            for child in node.children:
                if child.type == 'identifier':
                    func_name = get_node_text(child)
                    break

            if func_name:
                calls = []
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1

                def find_calls(body_node):
                    if body_node.type == 'call':
                        callee_node = body_node.children[0]
                        if callee_node.type == 'identifier':
                            calls.append(get_node_text(callee_node))
                        elif callee_node.type == 'attribute':
                            if len(callee_node.children) > 0:
                                last_child = callee_node.children[-1]
                                if last_child.type == 'identifier':
                                    calls.append(get_node_text(last_child))
                    
                    for child in body_node.children:
                        find_calls(child)

                # Locate block/body of function
                body_node = None
                for child in node.children:
                    if child.type == 'block':
                        body_node = child
                        break
                
                referenced_identifiers = []
                if body_node:
                    find_calls(body_node)
                    referenced_identifiers = get_identifiers(body_node)

                functions.append({
                    "name": func_name,
                    "class": current_class,
                    "file": filepath,
                    "calls": list(set(calls)),
                    "referenced_identifiers": referenced_identifiers,
                    "start_line": start_line,
                    "end_line": end_line
                })
                
            # Traverse nested code blocks
            for child in node.children:
                if child.type == 'block':
                    for block_child in child.children:
                        walk_tree(block_child, current_class)
        else:
            for child in node.children:
                walk_tree(child, current_class)

    walk_tree(root)

    return {
        "path": filepath,
        "classes": classes,
        "functions": functions,
        "top_level_assignments": top_level_assignments
    }

def parse_git_diff(diff_output: str) -> dict[str, list[int]]:
    """
    Parses git diff output and extracts modified line numbers in the new files.
    """
    modified_files = {}
    current_file = None
    current_line = 0

    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            modified_files[current_file] = []
            current_line = 0
        elif line.startswith("@@"):
            # Format: @@ -old_start,old_count +new_start,new_count @@
            try:
                parts = line.split("+")
                if len(parts) >= 2:
                    new_info = parts[1].split(" ")[0]
                    if "," in new_info:
                        current_line = int(new_info.split(",")[0])
                    else:
                        current_line = int(new_info)
            except Exception:
                current_line = 0
        elif current_file is not None:
            if line.startswith("+") and not line.startswith("+++"):
                if current_line > 0:
                    modified_files[current_file].append(current_line)
                current_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                # Deletion marks boundary at current line
                if current_line > 0:
                    modified_files[current_file].append(max(1, current_line))
            else:
                current_line += 1

    # De-duplicate and filter files
    for path in list(modified_files.keys()):
        modified_files[path] = sorted(list(set(modified_files[path])))
        if not modified_files[path]:
            del modified_files[path]

    return modified_files

def extract_file_diffs(diff_output: str) -> dict[str, str]:
    """
    Maps modified files to their respective git diff blocks.
    """
    file_diffs = {}
    current_file = None
    current_lines = []

    for line in diff_output.splitlines():
        if line.startswith("diff --git"):
            if current_file and current_lines:
                file_diffs[current_file] = "\n".join(current_lines)
            current_file = None
            current_lines = []
        elif line.startswith("+++ b/"):
            current_file = line[6:].strip()
            current_lines.append(line)
        elif current_file is not None:
            current_lines.append(line)

    if current_file and current_lines:
        file_diffs[current_file] = "\n".join(current_lines)

    return file_diffs

def get_modified_functions(baseline_commit: str = "HEAD") -> dict[str, list[dict]]:
    """
    Finds modified functions by overlapping git diff lines with tree-sitter AST scopes.
    """
    cmd = ["git", "diff", baseline_commit]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        diff_output = result.stdout
    except Exception:
        try:
            result = subprocess.run(["git", "diff"], capture_output=True, text=True, check=True)
            diff_output = result.stdout
        except Exception:
            return {}

    modified_lines = parse_git_diff(diff_output)
    if not modified_lines:
        return {}

    file_diffs = extract_file_diffs(diff_output)
    modified_functions_by_file = {}

    for file_path, lines in modified_lines.items():
        if not os.path.exists(file_path):
            continue

        symbols = extract_symbols(file_path)
        modified_funcs = []

        # 1. Identify which global/module-level variables were modified
        modified_globals = set()
        for tla in symbols.get("top_level_assignments", []):
            start = tla["start_line"]
            end = tla["end_line"]
            for line in lines:
                if start <= line <= end:
                    for var in tla["variables"]:
                        modified_globals.add(var)
                    break

        # 2. Check each function for direct or global modification
        for func in symbols.get("functions", []):
            start = func["start_line"]
            end = func["end_line"]
            
            is_modified = False
            # Check for direct modifications
            for line in lines:
                if start <= line <= end:
                    is_modified = True
                    break
            
            # Check for referenced global modifications
            if not is_modified and modified_globals:
                for ref_id in func.get("referenced_identifiers", []):
                    if ref_id in modified_globals:
                        is_modified = True
                        break

            if is_modified:
                modified_funcs.append({
                    "name": func["name"],
                    "class": func["class"],
                    "file": file_path,
                    "diff": file_diffs.get(file_path, "")
                })

        if modified_funcs:
            modified_functions_by_file[file_path] = modified_funcs

    return modified_functions_by_file

def parse_directory(directory: str) -> dict:
    """
    Crawl directory recursively, extracting AST symbols from all .py files.
    Generates a structured mapping layout for the Graph database populate routine.
    """
    all_files = []
    all_classes = []
    all_functions = []

    for root_dir, _, filenames in os.walk(directory):
        for filename in filenames:
            if filename.endswith(".py"):
                filepath = os.path.join(root_dir, filename)
                # Normalize path relative to workspace
                rel_path = os.path.relpath(filepath, directory)
                
                symbols = extract_symbols(filepath)
                
                all_files.append({"path": rel_path})
                
                for cls in symbols["classes"]:
                    all_classes.append({
                        "name": cls["name"],
                        "file": rel_path
                    })
                
                for fn in symbols["functions"]:
                    all_functions.append({
                        "name": fn["name"],
                        "class": fn["class"],
                        "file": rel_path,
                        "calls": fn["calls"]
                    })

    return {
        "files": all_files,
        "classes": all_classes,
        "functions": all_functions
    }

if __name__ == "__main__":
    print("Testing parser_engine.py AST extraction on itself...")
    # Parse itself
    symbols = extract_symbols(__file__)
    print(f"File parsed: {symbols['path']}")
    
    print("\nExtracted Classes:")
    for cls in symbols["classes"]:
        print(f" - {cls['name']}")
        
    print("\nExtracted Functions:")
    for fn in symbols["functions"]:
        print(f" - {fn['name']} (Lines {fn['start_line']}-{fn['end_line']}) calls: {fn['calls']}")

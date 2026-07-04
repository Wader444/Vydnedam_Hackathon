import os

def normalize_path(path: str, repo_root: str) -> str:
    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(abs_path, repo_root)
    return rel_path.replace(os.sep, "/")
import os

def normalize_path(path: str, repo_root: str) -> str:
    """Normalizes any path to a repo-relative, forward-slash format."""
    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(abs_path, repo_root)
    return rel_path.replace(os.sep, "/")
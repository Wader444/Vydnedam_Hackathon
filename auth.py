SESSION_DATA = {
    "uid": "usr_98765", # Changed user_id to uid (breaking change)
    "roles": ["developer", "reviewer"],
    "authenticated": True
}

def get_session():
    """
    Returns the current authentication session dictionary.
    """
    # Refactored session return
    return SESSION_DATA

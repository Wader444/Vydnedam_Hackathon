from auth import get_session

def render_welcome_message():
    """
    Simulates welcome message rendering. Depends explicitly on 'user_id'.
    """
    session = get_session()
    uid = session["user_id"]
    return f"Welcome, user: {uid}"

def display_dashboard():
    """
    Renders the app dashboard after checking credentials.
    """
    msg = render_welcome_message()
    print(f"--- Dashboard ---\n{msg}")

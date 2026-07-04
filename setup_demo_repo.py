import os
import subprocess

def run_cmd(cmd):
    subprocess.run(cmd, shell=True, check=True)

def main():
    # 1. Initialize git
    if not os.path.exists(".git"):
        run_cmd("git init")
        # Configure local git user for committing
        run_cmd("git config user.name 'Hackathon Dev'")
        run_cmd("git config user.email 'dev@impactgraph.local'")

    # 2. Write 10 auxiliary mock files to create a realistic call graph
    mock_files = {
        "database.py": """
from logging_helper import log_transaction

def execute_query(query_str):
    log_transaction("executing_query", query_str)
    return [{"status": "success"}]
""",
        "utils.py": """
from logging_helper import log_info

def format_timestamp(ts):
    log_info(f"Formatting timestamp {ts}")
    return f"formatted_{ts}"

def validate_cents(cents):
    return cents > 0
""",
        "config.py": """
def get_redis_uri():
    return "redis://localhost:6379/0"

def get_smtp_settings():
    return {"host": "smtp.gmail.com", "port": 587}
""",
        "routes.py": """
from frontend import display_dashboard

def handle_home_route():
    display_dashboard()
""",
        "models.py": """
from utils import format_timestamp

def create_user_model(user_data):
    ts = format_timestamp(12345678)
    return {"user": user_data, "created_at": ts}
""",
        "payment.py": """
from database import execute_query
from utils import validate_cents

def process_charge(amount_cents):
    if validate_cents(amount_cents):
        execute_query("INSERT INTO payments VALUES (" + str(amount_cents) + ")")
        return True
    return False
""",
        "notifications.py": """
from email_service import send_raw_email

def notify_user(email_addr, subject):
    send_raw_email(email_addr, subject, "Hi user!")
""",
        "logging_helper.py": """
def log_info(msg):
    print(f"[INFO] {msg}")

def log_transaction(action, details):
    print(f"[TX] {action}: {details}")
""",
        "cache.py": """
from config import get_redis_uri

def get_cached_val(key):
    uri = get_redis_uri()
    return None
""",
        "email_service.py": """
from config import get_smtp_settings

def send_raw_email(to_addr, subject, body):
    settings = get_smtp_settings()
    print(f"Sending email via {settings['host']} to {to_addr}")
"""
    }

    for name, content in mock_files.items():
        with open(name, "w") as f:
            f.write(content.strip() + "\n")

    # 3. Add and commit all files as baseline
    run_cmd("git add .")
    try:
        run_cmd("git commit -m 'Initial baseline commit'")
    except subprocess.CalledProcessError:
        pass

    # 4. Introduce the breaking key change in auth.py
    breaking_auth_content = """
SESSION_DATA = {
    "uid": "usr_98765", # Changed user_id to uid (breaking change)
    "roles": ["developer", "reviewer"],
    "authenticated": True
}

def get_session():
    \"\"\"
    Returns the current authentication session dictionary.
    \"\"\"
    # Refactored session return
    return SESSION_DATA
"""

    with open("auth.py", "w") as f:
        f.write(breaking_auth_content.strip() + "\n")

    print("Demo repository initialized successfully.")
    print("auth.py has been modified, changing 'user_id' to 'uid' (breaking key change).")

if __name__ == "__main__":
    main()

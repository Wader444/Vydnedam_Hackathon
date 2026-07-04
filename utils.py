from logging_helper import log_info

def format_timestamp(ts):
    log_info(f"Formatting timestamp {ts}")
    return f"formatted_{ts}"

def validate_cents(cents):
    return cents > 0

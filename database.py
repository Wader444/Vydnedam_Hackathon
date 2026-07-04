from logging_helper import log_transaction

def execute_query(query_str):
    log_transaction("executing_query", query_str)
    return [{"status": "success"}]

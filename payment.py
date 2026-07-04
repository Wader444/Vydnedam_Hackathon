from database import execute_query
from utils import validate_cents

def process_charge(amount_cents):
    if validate_cents(amount_cents):
        execute_query("INSERT INTO payments VALUES (" + str(amount_cents) + ")")
        return True
    return False

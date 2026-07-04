from utils import format_timestamp

def create_user_model(user_data):
    ts = format_timestamp(12345678)
    return {"user": user_data, "created_at": ts}

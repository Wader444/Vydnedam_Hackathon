from config import get_smtp_settings

def send_raw_email(to_addr, subject, body):
    settings = get_smtp_settings()
    print(f"Sending email via {settings['host']} to {to_addr}")

from email_service import send_raw_email

def notify_user(email_addr, subject):
    send_raw_email(email_addr, subject, "Hi user!")

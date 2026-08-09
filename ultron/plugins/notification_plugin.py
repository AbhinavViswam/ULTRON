from winotify import Notification

def send_toast(title: str, message: str, duration: int = 5):
    """
    Sends a native desktop toast notification.
    
    Args:
        title (str): The title of the notification.
        message (str): The main body text of the notification.
        duration (int): Ignored for winotify, defaults to short.
    """
    try:
        toast = Notification(
            app_id="Ultron",
            title=title,
            msg=message,
            duration="short"
        )
        toast.show()
    except Exception as e:
        print(f"[Notification Error] Failed to send desktop toast: {e}")

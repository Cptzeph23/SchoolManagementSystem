from .models import Notification


def dashboard_notifications(request):
    if not request.user.is_authenticated:
        return {"dashboard_notifications": [], "unread_notification_count": 0}
    notifications = Notification.objects.filter(recipient=request.user).order_by("-created_at")[:8]
    return {
        "dashboard_notifications": notifications,
        "unread_notification_count": Notification.objects.filter(recipient=request.user, is_read=False).count(),
    }

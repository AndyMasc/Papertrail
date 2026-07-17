def webpush_status(
    request,
):  # Returns True if user is authenticated and has an active webpush subscription
    subscription_count = 0
    if request.user.is_authenticated:
        subscription_count = request.user.webpush_info.count()

    return {
        "webpush_enabled": subscription_count > 0,
        "webpush_subscription_count": subscription_count,
    }

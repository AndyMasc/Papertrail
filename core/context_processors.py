"""Template context processors that inject webpush state into every request."""


def webpush_status(request):
    """Add webpush subscription status to the template context.

    Returns ``webpush_enabled`` (bool) and ``webpush_subscription_count``
    (int) so templates can conditionally show subscribe/unsubscribe UI.
    """
    subscription_count = 0
    if request.user.is_authenticated:
        subscription_count = request.user.webpush_info.count()

    return {
        "webpush_enabled": subscription_count > 0,
        "webpush_subscription_count": subscription_count,
    }

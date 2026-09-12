"""Views for the docs application: static documentation pages."""

from django.shortcuts import render


def how_to_use(request):
    """Render the how to use documentation page."""
    return render(request, "docs/how_to_use.html")


def pricing_doc(request):
    """Render the pricing documentation page."""
    return render(request, "docs/pricing_doc.html")


def privacy_policy(request):
    """Render the privacy policy page."""
    return render(request, "docs/privacy_policy.html")

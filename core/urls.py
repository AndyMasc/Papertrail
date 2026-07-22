"""URL configuration for the core application.

All routes live under the ``core`` namespace. The root path serves the landing
page for unauthenticated visitors and redirects to the dashboard for logged-in
users.
"""

from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.index, name="landing_page"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("privacy_policy/", views.privacy_policy, name="privacy_policy"),
    path("profile_page/", views.ProfilePageView.as_view(), name="profile_page"),
    path("health/", views.health_check, name="health_check"),
]

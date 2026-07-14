from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.index, name="landing_page"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("privacy_policy/", views.privacy_policy, name="privacy_policy"),
    path("profile_page/", views.ProfilePageView.as_view(), name="profile_page"),
]

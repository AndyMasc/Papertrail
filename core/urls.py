from django.urls import path
from . import views

app_name = 'core'
urlpatterns = [
    path('', views.index, name='home'),
    path('dashboard/', views.dashboard.as_view(), name='dashboard'),
    path('privacy_policy/', views.privacy_policy, name='privacy_policy'),
    path('profile_page/', views.profile_page, name='profile_page')
]

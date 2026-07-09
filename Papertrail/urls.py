from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponseForbidden

def forbidden_view(request, *args, **kwargs):
    return HttpResponseForbidden("Password features are disabled.")

urlpatterns = [
    path('admin/', admin.site.urls),

    path('__reload__/', include('django_browser_reload.urls')), # NOT FOR PRODUCTION

    # Block password management paths completely
    path("accounts/password/change/", forbidden_view),
    path("accounts/password/set/", forbidden_view),
    path("accounts/password/reset/", forbidden_view),
    
    # Include allauth normally for everything else
    path('accounts/', include('allauth.urls')),

    path('', include('core.urls')),
    path('documents/', include('documents.urls')),
    path('records/', include('records.urls')),
]

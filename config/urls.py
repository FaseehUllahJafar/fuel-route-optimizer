from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="route-map", permanent=False)),
    path("api/", include("routing.urls")),
    path("admin/", admin.site.urls),
]

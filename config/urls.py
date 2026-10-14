from django.urls import path

from fuelapp import views as fa

urlpatterns = [
    path("", fa.index),
    path("api/v1/plan", fa.plan_api),
    path("api/v1/health", fa.health),
    path("map", fa.map_page),
]

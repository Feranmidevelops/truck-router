from django.urls import path

from . import views

urlpatterns = [
    path("health", views.HealthView.as_view()),
    path("geocode", views.GeocodeView.as_view()),
    path("plan", views.PlanView.as_view()),
    path("plan/forecasts", views.PlanForecastsView.as_view()),
    path("heatmap", views.HeatmapView.as_view()),
    path("heatmap/forecasts", views.HeatmapForecastsView.as_view()),
]

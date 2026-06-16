from django.urls import path
from . import views
from . import mcr_views
from . import pr_views

urlpatterns = [
    path("sync-aptem-users/", views.sync_aptem_users, name="sync_aptem_users"),
    path("sync-mcr/", mcr_views.sync_mcr, name="sync_mcr"),
    path("sync-pr/", pr_views.sync_pr, name="sync_pr"),
]
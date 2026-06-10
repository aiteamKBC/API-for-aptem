from django.urls import path
from . import views

urlpatterns = [
    path("sync-aptem-users/", views.sync_aptem_users, name="sync_aptem_users"),
]
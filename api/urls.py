from django.urls import path
from . import views
from . import mcr_views
from . import pr_views

urlpatterns = [
    path("sync-aptem-users/", views.sync_aptem_users, name="sync_aptem_users"),
    path("sync-aptem-users/<int:user_id>/", views.sync_aptem_user, name="sync_aptem_user"),
    path("programme-info/", views.programme_info, name="programme_info"),
    path("programme-info/<int:user_id>/", views.programme_info_user, name="programme_info_user"),
    path("sync-mcr/", mcr_views.sync_mcr, name="sync_mcr"),
    path("sync-pr/", pr_views.sync_pr, name="sync_pr"),
]
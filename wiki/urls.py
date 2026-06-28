from django.urls import path

from . import views


urlpatterns = [
    path("", views.show_page, {"slug": "FrontPage"}, name="front"),
    path("_delete/<path:slug>", views.delete_page, name="delete"),
    path("_edit/<path:slug>", views.edit_page, name="edit"),
    path("_go", views.go_page, name="go"),
    path("_history/<path:slug>", views.history_page, name="history"),
    path("_index", views.index_page, name="index"),
    path("_raw/<path:slug>", views.raw_page, name="raw"),
    path("_recent", views.recent_page, name="recent"),
    path("_search", views.search_page, name="search"),
    path("<path:slug>", views.show_page, name="page"),
]

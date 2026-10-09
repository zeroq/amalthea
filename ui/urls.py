"""UI routes.

Every name carries a `ui-` prefix. The API and the UI share entity vocabulary but not route names, and
unprefixed names would collide with the `/api/v1/` ones in any shared template or `reverse()` call
made by someone who has read the other side of the codebase.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="ui-dashboard"),
    path("alerts", views.alert_list, name="ui-alert-list"),
    path("alerts/<uuid:alert_id>", views.alert_detail, name="ui-alert-detail"),
    path("alerts/<uuid:alert_id>/escalate", views.alert_escalate, name="ui-alert-escalate"),
    path("alerts/<uuid:alert_id>/merge", views.alert_merge, name="ui-alert-merge"),
    path("cases", views.case_list, name="ui-case-list"),
    path("cases/<str:case_id>", views.case_detail, name="ui-case-detail"),
    path("cases/<str:case_id>/status", views.case_set_status, name="ui-case-status"),
    path("cases/<str:case_id>/comment", views.case_comment, name="ui-case-comment"),
    path("cases/<str:case_id>/extract", views.case_extract_observables, name="ui-case-extract"),
    path("cases/<str:case_id>/task", views.case_task_create, name="ui-case-task-create"),
    path(
        "cases/<str:case_id>/task/<uuid:task_id>/toggle",
        views.case_task_toggle,
        name="ui-case-task-toggle",
    ),
    path("automation", views.automation_list, name="ui-automation-list"),
    path("automation/playbooks", views.playbook_list, name="ui-playbook-list"),
    path("automation/playbooks/new", views.playbook_create, name="ui-playbook-create"),
    path(
        "automation/playbooks/<str:playbook_id>", views.playbook_detail, name="ui-playbook-detail"
    ),
    path("automation/playbooks/<str:playbook_id>/run", views.playbook_run, name="ui-playbook-run"),
    path("automation/playbooks/<str:playbook_id>/delete", views.playbook_delete, name="ui-playbook-delete"),
    path("sources", views.sources_list, name="ui-sources-list"),
    path("login", views.SignInView.as_view(), name="login"),
    path("logout", views.sign_out, name="logout"),
]

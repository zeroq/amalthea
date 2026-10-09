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
    # Case sub-resources (P2-P5 UI)
    path("cases/<str:case_id>/export", views.case_export, name="ui-case-export"),
    path("cases/<str:case_id>/tags", views.case_tags, name="ui-case-tags"),
    path("cases/<str:case_id>/template", views.case_apply_template, name="ui-case-apply-template"),
    path("cases/<str:case_id>/merge", views.case_merge, name="ui-case-merge"),
    path("cases/<str:case_id>/bulk", views.case_bulk, name="ui-case-bulk"),
    path(
        "cases/<str:case_id>/attachment", views.case_attachment_list, name="ui-case-attachment-list"
    ),
    path(
        "cases/<str:case_id>/attachment/<uuid:attachment_id>/download",
        views.case_attachment_download,
        name="ui-case-attachment-download",
    ),
    path(
        "cases/<str:case_id>/attachment/<uuid:attachment_id>",
        views.case_attachment_detail,
        name="ui-case-attachment-detail",
    ),
    path(
        "cases/<str:case_id>/procedure",
        views.case_procedure_create,
        name="ui-case-procedure-create",
    ),
    path(
        "cases/<str:case_id>/procedures",
        views.case_procedures_create,
        name="ui-case-procedures-create",
    ),
    path("cases/<str:case_id>/export", views.case_export, name="ui-case-export"),
    # Case templates
    path("case-templates", views.case_template_list, name="ui-case-template-list"),
    path("case-templates/new", views.case_template_create, name="ui-case-template-create"),
    path(
        "case-templates/<str:template_id>",
        views.case_template_detail,
        name="ui-case-template-detail",
    ),
    path(
        "case-templates/<str:template_id>/delete",
        views.case_template_delete,
        name="ui-case-template-delete",
    ),
    # Tags
    path("tags", views.tag_list, name="ui-tag-list"),
    path("tags/new", views.tag_create, name="ui-tag-create"),
    path("tags/<str:tag_id>", views.tag_detail, name="ui-tag-detail"),
    path("tags/<str:tag_id>/delete", views.tag_delete, name="ui-tag-delete"),
    # Observables
    path("observables", views.observable_list, name="ui-observable-list"),
    path("observables/<str:observable_id>", views.observable_detail, name="ui-observable-detail"),
    # Taxonomy / vocabularies
    path("taxonomy", views.taxonomy, name="ui-taxonomy"),
    path("case-templates", views.case_template_list, name="ui-case-template-list"),
    # Procedures/TTP on case
    path(
        "cases/<str:case_id>/procedure",
        views.case_procedure_create,
        name="ui-case-procedure-create",
    ),
    path(
        "cases/<str:case_id>/procedures",
        views.case_procedures_create,
        name="ui-case-procedures-create",
    ),
    # Case export
    path("cases/<str:case_id>/export", views.case_export, name="ui-case-export"),
    # Automation/Playbooks
    path("automation", views.automation_list, name="ui-automation-list"),
    path("automation/playbooks", views.playbook_list, name="ui-playbook-list"),
    path("automation/playbooks/new", views.playbook_create, name="ui-playbook-create"),
    path(
        "automation/playbooks/<str:playbook_id>", views.playbook_detail, name="ui-playbook-detail"
    ),
    path("automation/playbooks/<str:playbook_id>/run", views.playbook_run, name="ui-playbook-run"),
    path(
        "automation/playbooks/<str:playbook_id>/delete",
        views.playbook_delete,
        name="ui-playbook-delete",
    ),
    # Sources
    path("sources", views.sources_list, name="ui-sources-list"),
    # Auth
    path("login", views.SignInView.as_view(), name="login"),
    path("logout", views.sign_out, name="logout"),
]

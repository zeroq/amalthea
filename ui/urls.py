"""UI routes.

Every name carries a `ui-` prefix. The API and the UI share entity vocabulary but not route names, and
unprefixed names would collide with the `/api/v1/` ones in any shared template or `reverse()` call
made by someone who has read the other side of the codebase.
"""

from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("", views.dashboard, name="ui-dashboard"),
    path("alerts", views.alert_list, name="ui-alert-list"),
    path("alerts/<uuid:alert_id>", views.alert_detail, name="ui-alert-detail"),
    path("alerts/<uuid:alert_id>/escalate", views.alert_escalate, name="ui-alert-escalate"),
    path("alerts/<uuid:alert_id>/merge", views.alert_merge, name="ui-alert-merge"),
    # P2/C1 — Alert status change
    path("alerts/<uuid:alert_id>/status", views.alert_status, name="ui-alert-status"),
    path("cases", views.case_list, name="ui-case-list"),
    # §10.2 — must precede `cases/<str:case_id>` (a literal is otherwise swallowed as a case id).
    path("cases/new", views.case_create, name="ui-case-create"),
    path("cases/<str:case_id>", views.case_detail, name="ui-case-detail"),
    path("cases/<str:case_id>/link-alert", views.case_alert_link, name="ui-case-alert-link"),
    path("cases/<str:case_id>/status", views.case_set_status, name="ui-case-status"),
    path("cases/<str:case_id>/close", views.case_close, name="ui-case-close"),
    path("cases/<str:case_id>/field", views.case_field_edit, name="ui-case-field-edit"),
    path("cases/<str:case_id>/comment", views.case_comment, name="ui-case-comment"),
    path("cases/<str:case_id>/extract", views.case_extract_observables, name="ui-case-extract"),
    path("cases/<str:case_id>/task", views.case_task_create, name="ui-case-task-create"),
    path(
        "cases/<str:case_id>/task/<uuid:task_id>/toggle",
        views.case_task_toggle,
        name="ui-case-task-toggle",
    ),
    # Task-scoped routes (CRUD + detail + notes)
    path("tasks/<uuid:task_id>", views.task_detail, name="ui-task-detail"),
    path("tasks/<uuid:task_id>/edit", views.case_task_edit, name="ui-case-task-edit"),
    path("tasks/<uuid:task_id>/delete", views.case_task_delete, name="ui-case-task-delete"),
    path("tasks/<uuid:task_id>/note", views.task_note, name="ui-task-note"),
    # P2/C2 — Case edit
    path("cases/<str:case_id>/edit", views.case_edit, name="ui-case-edit"),
    # Case sub-resources (P2-P5 UI)
    path("cases/<str:case_id>/export", views.case_export, name="ui-case-export"),
    path("cases/<str:case_id>/tags", views.case_tags, name="ui-case-tags"),
    path("cases/<str:case_id>/tags/add", views.case_tag_add, name="ui-case-tag-add"),
    path("cases/<str:case_id>/tags/remove", views.case_tag_remove, name="ui-case-tag-remove"),
    path(
        "cases/<str:case_id>/tags/<uuid:tag_id>/toggle",
        views.case_tag_toggle,
        name="ui-case-tag-toggle",
    ),
    path("cases/<str:case_id>/template", views.case_apply_template, name="ui-case-apply-template"),
    path("cases/<str:case_id>/merge", views.case_merge, name="ui-case-merge"),
    path("cases/<str:case_id>/shares", views.case_share_list, name="ui-case-share-list"),
    path(
        "cases/<str:case_id>/shares/<uuid:share_id>/revoke",
        views.case_share_revoke,
        name="ui-case-share-revoke",
    ),
    path(
        "cases/<str:case_id>/attachment", views.case_attachment_list, name="ui-case-attachment-list"
    ),
    # The specific `upload` / `.../delete` routes are registered before the generic
    # `attachment/<uuid:attachment_id>` ones so a literal segment can never be swallowed by a
    # converter the way a later, broader pattern would swallow it.
    path(
        "cases/<str:case_id>/attachment/upload",
        views.case_attachment_upload,
        name="ui-case-attachment-upload",
    ),
    path(
        "cases/<str:case_id>/attachment/<uuid:attachment_id>/delete",
        views.case_attachment_delete,
        name="ui-case-attachment-delete",
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
    # Case pages. The API (`POST /case/{id}/page`, `PATCH|DELETE …/{pageId}`) is JSON-only, so the
    # three analyst actions live here. `page_id` is a UUID converter like every other sub-resource id
    # in this file, so a malformed id never reaches a queryset.
    path("cases/<str:case_id>/page", views.case_page_create, name="ui-case-page-create"),
    path(
        "cases/<str:case_id>/page/<uuid:page_id>/edit",
        views.case_page_update,
        name="ui-case-page-edit",
    ),
    path(
        "cases/<str:case_id>/page/<uuid:page_id>/delete",
        views.case_page_delete,
        name="ui-case-page-delete",
    ),
    # Case-scoped observable CRUD (tab affordances). UUID converter so a malformed id 404s early.
    path(
        "cases/<str:case_id>/observable",
        views.case_observable_create,
        name="ui-case-observable-create",
    ),
    path(
        "cases/<str:case_id>/observable/<uuid:observable_id>/edit",
        views.case_observable_update,
        name="ui-case-observable-edit",
    ),
    path(
        "cases/<str:case_id>/observable/<uuid:observable_id>/remove",
        views.case_observable_remove,
        name="ui-case-observable-remove",
    ),
    # Case templates
    path("case-templates", views.case_template_list, name="ui-case-template-list"),
    path("case-templates/new", views.case_template_create, name="ui-case-template-create"),
    path(
        "case-templates/<str:template_id>",
        views.case_template_detail,
        name="ui-case-template-detail",
    ),
    path(
        "case-templates/<str:template_id>/edit",
        views.case_template_edit,
        name="ui-case-template-edit",
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
    # P2/C3 — Observable edit/delete
    path("observables/<str:observable_id>/edit", views.observable_edit, name="ui-observable-edit"),
    path(
        "observables/<str:observable_id>/delete",
        views.observable_delete,
        name="ui-observable-delete",
    ),
    # Taxonomy / vocabularies
    path("taxonomy", views.taxonomy, name="ui-taxonomy"),
    # Automation/Playbooks
    path("automation", views.automation_list, name="ui-automation-list"),
    path("automation/playbooks", views.playbook_list, name="ui-playbook-list"),
    path("automation/playbooks/new", views.playbook_create, name="ui-playbook-create"),
    path(
        "automation/playbooks/<str:playbook_id>/edit", views.playbook_edit, name="ui-playbook-edit"
    ),
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
    path(
        "password/change",
        views.PasswordChangeView.as_view(
            template_name="ui/password_change.html",
            success_url=reverse_lazy("ui-dashboard"),
        ),
        name="ui-password-change",
    ),
    # Settings (superAdmin only)
    path("settings", views.settings_home, name="ui-settings"),
    path("settings/sources", views.settings_sources, name="ui-settings-sources"),
    path("settings/sources/token", views.settings_token_create, name="ui-settings-token-create"),
    # ADR-009: a source is org-owned, so this route is gated by role *and* org (404, not 403).
    path(
        "settings/sources/<uuid:source_id>",
        views.settings_source_detail,
        name="ui-settings-source-detail",
    ),
    path(
        "settings/sources/<uuid:token_id>/revoke",
        views.settings_token_revoke,
        name="ui-settings-token-revoke",
    ),
    path(
        "settings/sources/<uuid:token_id>/deactivate",
        views.settings_token_deactivate,
        name="ui-settings-token-deactivate",
    ),
    path(
        "settings/sources/<uuid:token_id>/activate",
        views.settings_token_activate,
        name="ui-settings-token-activate",
    ),
    path(
        "settings/sources/<uuid:token_id>/delete",
        views.settings_token_delete,
        name="ui-settings-token-delete",
    ),
    path("settings/organisations", views.settings_organisations, name="ui-settings-organisations"),
    path(
        "settings/organisations/<uuid:org_id>/edit",
        views.settings_organisations_edit,
        name="ui-settings-organisations-edit",
    ),
    path(
        "settings/organisations/<uuid:org_id>/delete",
        views.settings_organisations_delete,
        name="ui-settings-organisations-delete",
    ),
    path("settings/users", views.settings_users, name="ui-settings-users"),
    path(
        "settings/users/<uuid:user_id>/edit",
        views.settings_users_edit,
        name="ui-settings-users-edit",
    ),
    path(
        "settings/users/<uuid:user_id>/delete",
        views.settings_users_delete,
        name="ui-settings-users-delete",
    ),
]

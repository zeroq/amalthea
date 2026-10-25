# Amalthea — Data Model (implemented)

Source of truth: `*/models.py`. All time fields are `UTC` (`TIME_ZONE="UTC"`, `USE_TZ=True`);
all PKs are UUIDv4 via `core.models.UUIDModel` (`id = UUIDField(primary_key=True,
default=uuid.uuid4, editable=False)`). `core.models.TimeStampedModel` provides `created_at`
(`auto_now_add`) and `updated_at` (`auto_now`). `AUTH_USER_MODEL = "identity.User"`,
`DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`.

Constraint/index **names** are quoted verbatim — they are asserted by conformance tests and matter.

---

## identity

### Organisation (`identity_organisation`, extends `UUIDModel` + `TimeStampedModel`)
- `name` — CharField(200), **unique** · `description` — TextField.
- `created_at` / `updated_at` — `DateTimeField` (auto_now_add / auto_now), added 2026-10-11
  (PLAN-2026-10-10 O1).

### User (`identity_user`; `AbstractUser` + `UUIDModel`)
- `login` — CharField(150), **unique**; populated from `username` in `save()` when empty.
- `role` — CharField(20), `choices = USER_ROLE_CHOICES` (`superAdmin/admin/analyst/viewer`),
  default `analyst`; Constraint `identity_user_role_valid`. **Renamed 2026-10-12** (ADR-006
  decision 1): the read-only role is `viewer`, not `read-only` — `identity/migrations/0008_role_viewer_rename.py`.
  The unrelated *share* vocabulary keeps its own spelling (`share.permissions.profileName = "read-only"`).
- `org` — FK → `Organisation`, `on_delete=SET_NULL`, `null=True`, `related_name="users"`. Kept
  DB-nullable for `createsuperuser`, but **deny-by-default** in the app layer: an org-less user has
  no tenant access (ADR-004).
- Docstring (ADR-002 §D3/D11): unknown assignee logins are **not** auto-created.

### IngestionToken (`identity_ingestion_token`, extends `UUIDModel` + `TimeStampedModel`) — ADR-004 Q6
- `source` — FK → `ingest.IngestionSource`, `CASCADE`, `db_index=False`, `related_name="tokens"`.
- `organisation` — FK → `Organisation`, `PROTECT`, `related_name="ingestion_tokens"`.
- `name`(200) · `prefix` (CharField(12), **unique**, first 12 chars of the raw token) ·
  `secret_hash` (argon2, plaintext shown once) · `revoked_at` (terminal) / `deactivated_at`
  (reversible pause, TODO §10a) / `last_used_at` — all nullable.
- Index `ingest_token_src_revoked_idx ("source","revoked_at")`. Binds a source to exactly one org;
  the webhook's `owner_org` comes from the token, never the body.
- **Invariant (ADR-009):** `organisation` must be the organisation that owns `source`. A cross-table
  CHECK cannot express it, so it is enforced in `IngestionToken.save()` and `create_token()`
  (`ValidationError`) — a mismatch is a cross-tenant ingest: another org's alerts written through this
  org's credential against the other org's mapping.

### AuditLog (`identity_auditlog`, extends `UUIDModel`) — ADR-004 Q5
- `actor` — FK → `User`, `SET_NULL`, `db_index=False` · `actor_org`/`target_org` — FK →
  `Organisation`, `SET_NULL` · `action`, `target_type`, `target_id` (CharFields) ·
  `details` — JSONB, `default=dict` (added 2026-10-16, deviation **MAP-7**; non-secret structured
  detail, today `{"changed": ["fields.title.paths", …]}` for a mapping edit — dot-paths into the
  changed object, **never values**) ·
  `ip` (GenericIPAddressField, nullable) · `created_at` (`auto_now_add`, indexed).
- Indexes `auditlog_actor_created_idx ("actor","-created_at")`,
  `auditlog_tgt_org_created_idx ("target_org","-created_at")`,
  `auditlog_action_created_idx ("action","-created_at")`. Records superAdmin cross-org reads
  (`cross_org_read`/`cross_org_read_query`); the designated home for role/org/share/token changes.

### ApiKey (`identity_apikey`, extends `UUIDModel` + `TimeStampedModel`)
- `user` — FK → `User`, `on_delete=CASCADE`, `null=True` · `name` — CharField.
- `prefix` — CharField(32), **unique** (first 12 chars of the raw token) · `key_hash` — CharField(255,
  argon2).
- `scope` — choices from `API_KEY_SCOPES = ("read", "readwrite")`, default `"readwrite"`.
- `revoked_at` / `last_used_at` — DateTimeField, nullable.
- Constraint: `apikey_scope_valid` (`scope` ∈ `API_KEY_SCOPES`).

## ingest

### IngestionSource (`ingestion_source`, extends `UUIDModel` + `TimeStampedModel`) — org-owned (ADR-009)
- `organisation` — FK → `Organisation`, `PROTECT`, `related_name="ingestion_sources"`, **NOT NULL**.
  The feed is tenant data (ADR-009 reverses ADR-008 §4), so an org that still owns one cannot be
  deleted, and `organisation_blockers` reports it as `ingestionSources` (the FK is the only
  DB-enforced half; the refusal is what makes it *explicable*).
- `slug` — **unique per organisation** (`ingestion_source_org_slug_uniq (organisation, slug)`) ·
  `name` · `mapping_config` — JSONB (the JSON-path mapping table, v1 flat or v2 `fields`).
- The slug left the webhook URL on 2026-10-15 (ADR-008): the route is `/api/v1/alerts/webhook/`
  and the token does the routing. `slug` is what a name typed in Settings → Sources resolves to
  (`slugify`, get-or-create **within the token's organisation**, ADR-009), and `webhook_secret_hash`
  is the legacy route a secret was presented on directly.
- `default_severity` — SmallInteger, default 2, choices `SEVERITY_CHOICES` 1..4.
- `correlation_enabled` — BooleanField, default true.
- Constraint: `ingestion_source_severity_range`.
- Read/write UI: `GET /settings/sources/<uuid>` (`ui-settings-source-detail`) — `admin` for its own
  organisation's feeds, `superAdmin` for any (audited `cross_org_read`), everyone else a uniform 404
  (ADR-006 gate before the ADR-009 org gate).
- The page has two POST intents that write the *same* field: the two save forms (`form=table|json`) and,
  beside them, "Apply to unlinked alerts…" (`intent=apply` → `intent=apply_confirmed`), which saves the
  config and re-maps the feed's own unlinked open alerts in one transaction — `Alerts` is where that
  lands, not `IngestionSource` (§8 P7 of the plan; MAP-9/MAP-10/MAP-11).

## alerts

### AlertStatus (`alert_status`)
- `value` — **unique, mutable** (rename allowed; `Alert.status` is an FK, so references follow) ·
  `stage` — choices `ALERT_STAGE_CHOICES` (`New/InProgress/Closed/Imported`), **immutable**.
- `order`, `description`, `hidden`. Constraint: `alert_status_stage_valid`.
- Global vocabulary, not per-organisation. Mutations are `superAdmin`-only; in-use rows cannot be
  deleted (`Alert.status` is `PROTECT`). Rename/new-value edits are audited.
- (Deviations M12 / plan §13: statuses are **DB rows**, not free enums; `Imported` stage exists.)
  Seeded rows: `New`(1), `Triaged`(InProgress,2), `Dismissed`(Closed,3), `Imported`(4), and
  `Closed`(Closed,5) added 2026-10-17 (§12.8(5), deviation **V-2**) — case close-propagation
  targets the dedicated `Closed` row, not `Dismissed`.

### Alert (`alert`, extends `UUIDModel` + `TimeStampedModel`)
- `type`(100) · `source`(100) — **untrusted wire string** · `source_ref`(255) · `external_link`(500)
  · `title`(500) · `description` · `severity` (SmallInteger, default 2, range 1..4).
- `status` — FK → `AlertStatus`, `on_delete=PROTECT`, `db_index=False`.
- `date` — DateTimeField, `default=timezone.now`, **NOT NULL**.
- `tags` — M2M → `cases.Tag` through `AlertTagLink`.
- `flag` · `tlp` (default 2) · `pap` (default 2) · `summary`.
- `assignee` — FK → `User`, `SET_NULL`, `related_name="assigned_alerts"`.
- `raw_payload` — JSONB, `default=dict` (never inlined in API output; own `/raw` endpoint).
- `correlation_key` — CharField(256), blank.
- `ingestion_source` — FK → `IngestionSource`, `SET_NULL` — **independent of `source`** (M3).
- `case` — FK → `cases.Case`, `SET_NULL`, `related_name="alerts"`.
- `verdict` — CharField(32), choices `CASE_VERDICT_CHOICES` (`undefined`, `true_positive`,
  `false_positive`), default `undefined`, constraint `alert_verdict_valid`. Set by close-propagation
  when the alert's case closes (§12.8(5), deviation **V-2**); `undefined` means "not yet decided".
- `follow` · `ingestion_warnings` — JSONB · `owner_org` — FK → `Organisation`, **`NOT NULL`**,
  `on_delete=PROTECT` (ADR-004; mandatory per-org row).
- `ingestion_warnings` carries two *differently treated* keys (MAP-2b): `"mapping"` is the structured
  `FieldWarning` list that describes how the current fields were resolved, so a **re-map replaces** it;
  `"warnings"` is the alert's event log (a synthesised `source_ref`, `status_created`, a severity
  fallback), so a re-map **appends** one `remapped:` line and rewrites nothing. A row is only ever
  written when the re-map changed a mapped field **or** the structured record was out of date — the
  second case is a write with no field change and no log line, reported as `refreshed` (**MAP-12**).
  Re-apply lives in `ingest/remap.py` (`REMAP_FIELDS` = title, description, summary, severity, date —
  see MAP-9/MAP-10) and *does* move `updated_at` (`auto_now` is named in `update_fields`).
  Each entry is a `FieldWarning` (`{"field", "reason", "paths"?, "value_preview"?}`) whose `reason` is a
  stable token — `not_found`/`unknown_value`/`bad_type`/`bad_transform`/`truncated` — with the first four
  meaning "no value was produced" (`ingest.mapping.UNRESOLVED_REASONS`). That distinction is what makes
  the page and the re-map dialog able to say *why* a field fell back, which the append-only log cannot.
- Indexes: `alert_status_date_idx ("status","-date")`, `alert_corrkey_date_idx
  ("correlation_key","date")`, `alert_created_at_idx ("-created_at")`.
- Constraints: `uniq_alert_source_type_ref (owner_org,source,type,source_ref)` — per-org dedupe
  (was `(source,type,source_ref)` until 2026-10-10), `alert_severity_range`, `alert_tlp_range`,
  `alert_pap_range`, `alert_verdict_valid`.

### AlertCustomFieldValue (`alert_custom_field_value`)
- `alert` (FK) · `custom_field` → `cases.CustomField` (the FK keeps its implicit index — REVIEW H-5).
- `order`, `value` — JSONB. Constraint: `uniq_alert_custom_field (alert, custom_field)`.

### AlertObservable (`alert_observable`)
- `alert` (FK, `db_index=False`) · `observable` (FK, `db_index=False`) · `added_by` (FK) ·
  `created_at`.
- Index `alertobs_obs_alert_idx ("observable","alert")`; constraint `uniq_alert_observable`.
- (Deviation P10-4: linking an alert observable does **not** dispatch automation; the case path owns
  dispatch.)

### AlertTagLink (`alert_tags`)
- `alert` (`db_index=False`), `tag`; `unique_together (("alert","tag"))`.

## cases

### CaseStatus (`case_status`)
- `value` unique, **mutable** (rename allowed; `Case.status` is an FK, so references follow) ·
  `stage` — `CASE_STAGES ("New","InProgress","Closed")`, **immutable** · `order`, `description`,
  `hidden`. Constraint: `case_status_stage_valid`.
- Global vocabulary, not per-organisation. Mutations are `superAdmin`-only; in-use rows cannot be
  deleted (`Case.status` is `PROTECT`). Rename/new-value edits are audited.

### Tag (`tag`)
- `name` unique · `colour` · `description`.

### CustomField (`custom_field`)
- `name` unique · `group` · `type` — `CUSTOM_FIELD_TYPES
  ("string","integer","float","boolean","date","url")` · `options` — JSONB.
- Constraint: `custom_field_type_valid`.

### Case (`case_record` — not `case`, reserved SQL word, deviation M6; extends `UUIDModel` + `TimeStampedModel`)
- `number` — `AllocatedNumberField` (unique, `editable=False`; allocated by
  `cases/numbering.py::allocate_case_number`, **not** caller-supplied — deviation).
- `title`(500) · `description` · `severity` (default 2) · `status` — FK → `CaseStatus`, `PROTECT`,
  `db_index=False`.
- `assignee` — FK → `User` · `tags` — M2M through `CaseTagLink` · `flag` · `tlp` · `pap` · `summary`.
- **Classification fields (PLAN-2026-10-14):**
  - `scope` — CharField(32), choices `CASE_SCOPE_CHOICES` (`unset`, `single_asset_user`,
    `multiple_assets_users`, `single_department`, `multiple_departments`, `complete_enterprise`,
    `third_party_supplier`), default `unset`. Constraint `case_scope_valid`.
  - `case_type` — CharField(32), choices `CASE_TYPE_CHOICES` (`unset`, `malware`, `phishing`,
    `password_leak`, `ransomware`, `bec`), default `unset`. Constraint `case_type_valid`.
  - `data_classification` — CharField(32), choices `DATA_CLASSIFICATION_CHOICES` (`unset`,
    `no_data`, `public_data`, `personal_data`, `sensitive_data`), default `unset`.
    Constraint `case_data_classification_valid`. **Writes are admin/superAdmin only** (Q-D5).
  - `phase` — CharField(32), choices `CASE_PHASE_CHOICES` (`unset`, `initial_triage`,
    `containment`, `analysis`, `remediation`, `cleanup`, `close`), default `initial_triage`.
    Constraint `case_phase_valid`.
  - `verdict` — CharField(32), choices `CASE_VERDICT_CHOICES` (`undefined`, `true_positive`,
    `false_positive`), default `undefined`. Constraint `case_verdict_valid`. Answers "was this a
    real incident?"; a case **cannot enter stage `Closed`** while it is `undefined` and the close
    carries no comment — the rule lives in `cases/update.py::enforce_close_rule` (§12.8(4),
    deviation **V-1** for the token spelling, **V-2** for close-propagation).

**Close-rule boundary (SECURITY-2026-10-18 M2):** The close rule (non-`undefined` verdict + non-blank
closing comment) is a *service-layer invariant* enforced at three entry points:
`apply_case_update` (single PATCH / UI inline edit), `case_bulk_update` (bulk PATCH), and
`create_case` (create-to-Closed). It **does not** constrain direct ORM writes
(`Case.save()`, `Case.objects.update()`), management commands, shell access, or raw SQL.
A direct write that sets `status` to a `Closed`-stage value will persist `closed_date` (via
`Case.save().stamp_closed_date()`) but leave `verdict="undefined"` and no closing comment.
This is a documented architectural decision, not a bug — the service layer is the enforced
boundary for all user-reachable paths.
- `start_date` (default now, NOT NULL) · `end_date` · `closed_date` · `owner_org` — FK →
  `Organisation`, **`NOT NULL`**, `on_delete=PROTECT` (ADR-004) · `ingestion_warnings` — JSONB.
- Manager: `AllocatingQuerySet.as_manager()` (number allocation covers `save` **and** `bulk_create`).
- `Case.save()`: allocates `number` on first insert (`ALLOCATED_MARKER`), then
  `stamp_closed_date()` sets `closed_date` **once**, on entry to stage `Closed`. The value is only
  persisted by callers that include `closed_date` in `update_fields`; `apply_case_update` does so on
  close (and clears it on reopen) — before 2026-10-17 a close through the API/edit modal left it
  NULL (defect fixed with PLAN-2026-10-17, §12.8 decision D4a).
- Indexes: `case_status_start_idx ("status","-start_date")`, `case_severity_idx ("-severity")`,
  `case_created_at_idx ("-created_at")`.
- Constraints: `case_severity_range`, `case_tlp_range`, `case_pap_range`,
  `case_scope_valid`, `case_type_valid`, `case_data_classification_valid`, `case_phase_valid`,
  `case_verdict_valid`.

**`Case.status.stage` vs `Case.phase` (Q-D2 / R1 mitigation):** `CaseStatus.stage` is the stable
3-bucket *openness* axis (`New`, `InProgress`, `Closed`) that automation triggers and `CASE_STAGES`
key on. `Case.phase` is an independent IR-lifecycle enum (`initial_triage` … `close`) recording the
analyst's position in the incident-response loop. The two axes are deliberately separate: changing
`phase` does not move `stage`, and automation must not read `phase` for trigger decisions.

### Task (`task`, extends `UUIDModel` + `TimeStampedModel`)
- `case` — FK → `Case`, `CASCADE` · `title`(500) · `description` · `group`(100).
- `status` — CharField default `"Waiting"`, `TASK_STATUS_CHOICES
  ("Waiting","InProgress","Completed","Cancel")` — **no `Todo`** (P10-8 fix).
- `flag` · `assignee` — FK → `User` · `order` · `due_date` · `started_at` · `ended_at` ·
  `mandatory`.
- Index `task_created_at_idx ("-created_at")`; constraint `task_status_valid`.

### TimelineEvent (`timeline_event`, extends `UUIDModel` + `TimeStampedModel`)
- `case` — FK, `db_index=False` · `date` · `end_date` · `title`(500) · `description`.
- `kind` — default `"comment"` (see `realtime.md` for the full kind map) · `actor` — FK → `User` ·
  `metadata` — JSONB.
- Index `timeline_case_date_idx ("case","-date","-id")` — trailing `-id` gives keyset determinism
  (deviation M9).

### CaseCustomFieldValue (`case_custom_field_value`)
- `case` (`db_index=False`) · `custom_field` (right-hand column, **keeps** index) · `order` · `value`
  — JSONB. Constraint: `uniq_case_custom_field`.

### CaseObservable (`case_observable`)
- `case`/`observable` both `db_index=False` · `added_by` · `created_at`.
- Index `caseobs_obs_case_idx ("observable","case")`; constraint `uniq_case_observable`.

### CaseTagLink (`case_record_tags`)
- `case` (`db_index=False`), `tag`; `unique_together (("case","tag"))`.

### Comment (`comment`, extends `UUIDModel` + `TimeStampedModel`) — T2 P2
- `case` — FK → `Case`, `CASCADE`, nullable, `db_index=False` · `alert` — FK → `Alert`, `CASCADE`,
  nullable, `db_index=False` · `message` (Text) · `created_by`/`updated_by` — FK → `User`,
  `SET_NULL` (audit, not ownership).
- **Exactly one parent**: CHECK `comment_one_parent` makes "no parent" and "two parents"
  unrepresentable. Indexes `comment_case_created_idx ("case","-created_at","-id")` and
  `comment_alert_created_idx ("alert","-created_at","-id")`.

### Page (`page`, extends `UUIDModel` + `TimeStampedModel`) — T2 P2
- `case` — FK → `Case`, `CASCADE`, `db_index=False` · `title`(500) · `content` (Text, Markdown) ·
  `order` (default 0) · `category`(100) · `created_by`/`updated_by` — FK → `User`, `SET_NULL`.
- Index `page_case_order_idx ("case","order","created_at")`.

### Share (`share`, extends `UUIDModel` + `TimeStampedModel`) — T2 P2
- `case` — FK → `Case`, `CASCADE`, `db_index=False` · `organisation` — FK → `Organisation`,
  `CASCADE` · `permissions` — JSONB `{"read": true, "write": bool}` · `created_by` — FK → `User`,
  `SET_NULL`.
- Constraint `uniq_share_case_org (case, organisation)`. Shares are **case-only** and only ever
  *add* access for one organisation; `Share.can_write` is the guard's read (`P2-5`, P2-3).

### Attachment (`attachment`, extends `UUIDModel` + `TimeStampedModel`) — T2 P3
- `case` — FK → `Case`, `CASCADE`, `db_index=False` · `name`(255, the original filename, display
  only) · `content_type`(128) · `size` (BigInteger) · `sha256`(64) · `path`(255, opaque storage key)
  · `external` (default False) · `created_by`/`updated_by` — FK → `User`, `SET_NULL`.
- Indexes `attach_case_created_idx ("case","-created_at","-id")` and `attach_sha256_idx ("sha256")`.
- `path` is generated server-side (`<ATTACHMENT_STORAGE_PREFIX>/<uuid4hex><ext>`); the client's
  filename is **never** a path component. `hashes=[sha256]` is emitted on the wire.

### CaseTemplate (`case_template`, extends `UUIDModel` + `TimeStampedModel`) — T2 P4
- `name` **unique** · `display_name` · `title_prefix` · `description` · `summary` · `flag`.
- `severity` / `tlp` / `pap` — SmallInteger with the same range CHECKs as `Case`.
- **Classification fields (Q-D6):** `scope` (choices `CASE_SCOPE_CHOICES`, default `unset`,
  constraint `case_template_scope_valid`), `case_type` (choices `CASE_TYPE_CHOICES`, default `unset`,
  constraint `case_template_case_type_valid`), `phase` (choices `CASE_PHASE_CHOICES`, default
  `initial_triage`, constraint `case_template_phase_valid`). **No `data_classification`** — that is
  a per-incident judgement and a template must not set or overwrite it.
- `tags` — JSONB list · `tasks` — JSONB list of task definitions · `custom_fields` — JSONB list.
- Applying a template **copies** these definitions into the case (tasks/tag links/custom-field
  values); nothing is shared with the template row (AC6.1-P4-c).
- A task definition's `description` may contain the literal token `{{ alert.raw_payload }}`. On
  apply, `cases.template_defaults.render_source_alert_payload` replaces it with the earliest alert
  linked to the case (same organisation; ordered by `date`, `created_at`, `id`) as pretty JSON. The
  JSON is capped at 64 KiB UTF-8 bytes, wrapped in a dynamically sized fenced block, and followed
  by a visible truncation marker only when capped. An invisible Markdown link-reference sentinel
  (`[//]: # (amalthea:source-alert-payload)`) is appended once so the task page can offer the full
  payload modal. The full payload is not stored on the task; the modal reads it from the alert and
  is restricted by `resolve_alert`.
- `custom_fields` items are applied only when a global `CustomField` with the same name already
  exists; a template never creates the vocabulary. The UI create form pre-fills an `Example Field`
  (`string`, value `example`) and a `Triage/Analysis` task; API-created templates stay explicit.
- No schema change or migration: the token and sentinel live inside the JSON/Markdown text.

### TTP (`ttp`, extends `UUIDModel` + `TimeStampedModel`) — T2 P5
- `name` **unique** · `ttp_code`(64) · `tactic`(100) · `description` (Text).
- Amalthea extension (no TheHive route); `Procedure.ttp` is `SET_NULL` so retiring a technique
  keeps the observed procedure. DELETE is refused while any procedure references it (AC6.1-P5-b).

### Procedure (`procedure`, extends `UUIDModel` + `TimeStampedModel`) — T2 P5
- `case` — FK → `Case`, `CASCADE`, nullable, `db_index=False` · `alert` — FK → `Alert`, `CASCADE`,
  nullable, `db_index=False` · `ttp` — FK → `TTP`, `SET_NULL`, nullable.
- `occur_date` · `pattern_id`(255) · `pattern_name`(255) · `tactic`(100) · `description` (Text).
- **Exactly one parent**: CHECK `procedure_exactly_one_parent` (mirrors `Comment`). Indexes
  `procedure_case_date_idx ("case","occur_date","id")` and
  `procedure_alert_date_idx ("alert","occur_date","id")`.

## observables

### ObservableType (`observable_type`)
- `name` unique, **mutable** (rename allowed; `Observable.data_type` is an FK) · `is_attachment` ·
  `is_case_sensitive` (default False).
- `save()` intercepts an `is_case_sensitive` change and re-hashes all matching observables in one
  `transaction.atomic()` via `observables/rehash.py::rehash_for_type` (REVIEW round-3 H3-1).
- Global vocabulary. Mutations are `superAdmin`-only; in-use rows cannot be deleted.

### Observable (`observable`, extends `UUIDModel` + `TimeStampedModel`)
- `data_type` — FK → `ObservableType`, `PROTECT`, `db_index=True` · `owner_org` — FK →
  `Organisation`, **`NOT NULL`**, `on_delete=PROTECT`, `db_index=False` (ADR-004) · `data` ·
  `normalized_data`.
- `data_hash` — `DataHashField` (sha256 hex of `normalized_data`, `max_length=64`,
  `editable=False`; recomputed on **every** INSERT incl. `bulk_create` — `observables/hashing.py`).
- `tags` — M2M through `ObservableTagLink` · `ioc` · `sighted` · `sighted_at` ·
  `ignore_similarity` · `message` · `tlp` · `pap` · `enrichment_data` — JSONB · `external`.
- Constraint: **`uniq_obs_org_dtype_hash (owner_org, data_type, data_hash)`** — observables are
  deduplicated **per organisation** (globally until 2026-10-10; ADR-004), so the same value in two
  orgs is two rows and case auto-linking is confined to one org.
- Constraints `observable_tlp_range`, `observable_pap_range`; indexes `obs_created_at_idx
  ("-created_at")`, `obs_org_created_idx ("owner_org","-created_at")`.
- `Observable.save()` appends `"data_hash"` to `update_fields` when the save is partial and touches
  `normalized_data` or `data_type`.

### ObservableTagLink (`observable_tags`)
- `observable` (`db_index=False`), `tag`; `unique_together (("observable","tag"))`.

## automation

### Playbook (`playbook`, extends `UUIDModel` + `TimeStampedModel`)
- `name` unique · `description` · `trigger_event`(100) · `is_active` (default True) · `config` —
  JSONB.
- Index `playbook_trigger_idx ("trigger_event","is_active")`.

### AutomationRun (`automation_run`, extends `UUIDModel` + `TimeStampedModel`)
- `case` — FK → `Case`, `SET_NULL`, `db_index=False`, `related_name="automation_runs"` ·
  `alert` — FK, `SET_NULL`.
- `playbook` — FK → `Playbook`, `PROTECT`, `related_name="runs"` · `playbook_name` — immutable
  snapshot (deviation M14).
- `trigger_event` · `status` — default `"Pending"`, `AUTOMATION_RUN_STATUS_CHOICES
  ("Pending","Running","Success","Failed")`.
- **`triggered_by` — CharField(20), default `"trigger"`, choices `AUTOMATION_RUN_TRIGGERED_BY_CHOICES`
  (`"trigger"`/`"manual"`). Distinguishes event-driven runs from analyst-initiated ones (plan §5.1).
  Constraint: `automation_run_triggered_by_valid`.**
- `output_log` · `error` · `triggered_by_observable` — FK, `SET_NULL` · `celery_task_id` ·
  `started_at` · `finished_at` · `idempotency_key` — CharField(255), **unique**.
- Indexes: `ar_case_started_idx ("case","-started_at")`, `ar_celery_task_idx ("celery_task_id")`,
  **partial `ar_pending_idx ("created_at") WHERE status='Pending'`** — the dispatch-sweep index
  (planner, not just catalog, is asserted in `tests/conformance/test_indexes.py`).
- Constraints: `automation_run_status_valid`, `automation_run_triggered_by_valid`.

## Core value sets (`core/enums.py`)

`SEVERITY_CHOICES` 1..4 (`Low/Medium/High/Critical`) · `TLP_CHOICES` 0..4
(`White/Green/Amber/Red/Unknown`) · `PAP_CHOICES` 0..3 · `TASK_STATUS_CHOICES`
(`Waiting/InProgress/Completed/Cancel`) · `AUTOMATION_RUN_STATUS_CHOICES`
(`Pending/Running/Success/Failed`) · `CASE_STAGES` (`New/InProgress/Closed`) · `ALERT_STAGES`
(`New/InProgress/Closed/Imported`) · `CUSTOM_FIELD_TYPES` · `API_KEY_SCOPES` (`read/readwrite`) ·
`USER_ROLE_CHOICES` (`superAdmin/admin/analyst/viewer`).
Builders `in_range(...)`/`in_values(...)` emit the CheckConstraints quoted above.

## Tenant isolation (ADR-004)

Tenancy is **strict and mandatory**: `Case`/`Alert`/`Observable.owner_org` are `NOT NULL` with
`on_delete=PROTECT`, and all scoping runs through **`identity/scoping.py`** (`case_visible_q`,
`org_q`, `org_filtered`, `resolve_case`, `resolve_alert`, `resolve_observable`, `is_case_owner`).
There is **no middleware org-scope and no Postgres RLS** — cross-tenant access is the same 404 as
"does not exist", `Share` is the only sanctioned cross-org read, and `superAdmin` cross-org reads are
written to `identity_auditlog`. See [`identity-auth.md`](./identity-auth.md) and
[`../decisions/ADR-004-tenant-isolation.md`](../decisions/ADR-004-tenant-isolation.md).

## Evidence

Conformance pins: `tests/conformance/test_phase3_schema.py`, `test_enum_contracts.py`,
`test_indexes.py`, and the T2 model suites `test_t2_p1_surface.py`..`test_t2_p5_surface.py`
(Comment/Page/Share/Attachment/CaseTemplate/TTP/Procedure; migrations `cases/0009`–`0014`,
`automation/0004`–`0006`). Tenant-isolation schema pins: migrations `identity/0004`–`0006`,
`cases/0015`, `alerts/0013`–`0014`, `observables/0007`, exercised by
`tests/conformance/test_tenant_scoping.py`. Behaviour pins (numbering, hashing, status semantics):
`tests/conformance/test_case_numbering.py`, `test_observable_hashing.py`,
`test_mutation_standard.py`, `test_seed_migrations.py`. FK integrity: `test_fk_audit.py`. GIN JSONB
indexes (§6.2) are Postgres-only. Deviations register: [`deviations.md`](./deviations.md).
Independent verification: [`../planning/VERIFY-2026-10-10-t2-and-phase12.md`](../planning/VERIFY-2026-10-10-t2-and-phase12.md).
Postgres-only semantics (partial index, sequences, CHECK): plan §12 R4/R10 and
`docs/planning/VERIFY-2026-10-07-phase10.md`.
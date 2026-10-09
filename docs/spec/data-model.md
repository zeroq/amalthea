# Amalthea — Data Model (implemented)

Source of truth: `*/models.py`. All time fields are `UTC` (`TIME_ZONE="UTC"`, `USE_TZ=True`);
all PKs are UUIDv4 via `core.models.UUIDModel` (`id = UUIDField(primary_key=True,
default=uuid.uuid4, editable=False)`). `core.models.TimeStampedModel` provides `created_at`
(`auto_now_add`) and `updated_at` (`auto_now`). `AUTH_USER_MODEL = "identity.User"`,
`DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`.

Constraint/index **names** are quoted verbatim — they are asserted by conformance tests and matter.

---

## identity

### Organisation (`identity_organisation`, extends `UUIDModel`)
- `name` — CharField(200), **unique** · `description` — TextField.

### User (`identity_user`; `AbstractUser` + `UUIDModel`)
- `login` — CharField(150), **unique**; populated from `username` in `save()` when empty.
- `role` — CharField. `org` — FK → `Organisation`, `on_delete=SET_NULL`, `related_name="users"`.
- Docstring (ADR-002 §D3/D11): unknown assignee logins are **not** auto-created.

### ApiKey (`identity_apikey`, extends `UUIDModel` + `TimeStampedModel`)
- `user` — FK → `User`, `on_delete=CASCADE`, `null=True` · `name` — CharField.
- `prefix` — CharField(32), **unique** (first 12 chars of the raw token) · `key_hash` — CharField(255,
  argon2).
- `scope` — choices from `API_KEY_SCOPES = ("read", "readwrite")`, default `"readwrite"`.
- `revoked_at` / `last_used_at` — DateTimeField, nullable.
- Constraint: `apikey_scope_valid` (`scope` ∈ `API_KEY_SCOPES`).

## ingest

### IngestionSource (`ingestion_source`, extends `UUIDModel` + `TimeStampedModel`)
- `slug` — **unique** · `name` · `mapping_config` — JSONB (the JSON-path mapping table).
- `default_severity` — SmallInteger, default 2, choices `SEVERITY_CHOICES` 1..4.
- `correlation_enabled` — BooleanField, default True.
- `webhook_secret_hash` — CharField(255), blank; hasher-backed secret (argon2; legacy plaintext
  compare supported in `ingest/views.py::_authenticate`).
- Constraint: `ingestion_source_severity_range`.

## alerts

### AlertStatus (`alert_status`)
- `value` — **unique** · `stage` — choices `ALERT_STAGE_CHOICES` (`New/InProgress/Closed/Imported`).
- `order`, `description`, `hidden`. Constraint: `alert_status_stage_valid`.
- (Deviations M12 / plan §13: statuses are **DB rows**, not free enums; `Imported` stage exists.)

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
- `follow` · `ingestion_warnings` — JSONB · `owner_org` — FK → `Organisation`, `SET_NULL`.
- Indexes: `alert_status_date_idx ("status","-date")`, `alert_corrkey_date_idx
  ("correlation_key","date")`, `alert_created_at_idx ("-created_at")`.
- Constraints: `uniq_alert_source_type_ref (source,type,source_ref)`, `alert_severity_range`,
  `alert_tlp_range`, `alert_pap_range`.

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
- `value` unique · `stage` — `CASE_STAGES ("New","InProgress","Closed")` · `order`,
  `description`, `hidden`. Constraint: `case_status_stage_valid`.

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
- `start_date` (default now, NOT NULL) · `end_date` · `closed_date` · `owner_org` — FK →
  `Organisation` · `ingestion_warnings` — JSONB.
- Manager: `AllocatingQuerySet.as_manager()` (number allocation covers `save` **and** `bulk_create`).
- `Case.save()`: allocates `number` on first insert (`ALLOCATED_MARKER`), then
  `stamp_closed_date()` sets `closed_date` **once**, on entry to stage `Closed`.
- Indexes: `case_status_start_idx ("status","-start_date")`, `case_severity_idx ("-severity")`,
  `case_created_at_idx ("-created_at")`.
- Constraints: `case_severity_range`, `case_tlp_range`, `case_pap_range`.

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
- `tags` — JSONB list · `tasks` — JSONB list of task definitions · `custom_fields` — JSONB list.
- Applying a template **copies** these definitions into the case (tasks/tag links/custom-field
  values); nothing is shared with the template row (AC6.1-P4-c).

## observables

### ObservableType (`observable_type`)
- `name` unique · `is_attachment` · `is_case_sensitive` (default False).
- `save()` intercepts an `is_case_sensitive` change and re-hashes all matching observables in one
  `transaction.atomic()` via `observables/rehash.py::rehash_for_type` (REVIEW round-3 H3-1).

### Observable (`observable`, extends `UUIDModel` + `TimeStampedModel`)
- `data_type` — FK → `ObservableType`, `PROTECT`, `db_index=False` · `data` · `normalized_data`.
- `data_hash` — `DataHashField` (sha256 hex of `normalized_data`, `max_length=64`,
  `editable=False`; recomputed on **every** INSERT incl. `bulk_create` — `observables/hashing.py`).
- `tags` — M2M through `ObservableTagLink` · `ioc` · `sighted` · `sighted_at` ·
  `ignore_similarity` · `message` · `tlp` · `pap` · `enrichment_data` — JSONB · `external`.
- Constraint: **`uniq_obs_dtype_hash (data_type, data_hash)`** — observables are deduplicated
  **globally**, not per-case (deviation vs AGENTS.md §3).
- Constraints `observable_tlp_range`, `observable_pap_range`; index `obs_created_at_idx
  ("-created_at")`.
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
- `output_log` · `error` · `triggered_by_observable` — FK, `SET_NULL` · `celery_task_id` ·
  `started_at` · `finished_at` · `idempotency_key` — CharField(255), **unique**.
- Indexes: `ar_case_started_idx ("case","-started_at")`, `ar_celery_task_idx ("celery_task_id")`,
  **partial `ar_pending_idx ("created_at") WHERE status='Pending'`** — the dispatch-sweep index
  (planner, not just catalog, is asserted in `tests/conformance/test_indexes.py`).
- Constraint: `automation_run_status_valid`.

## Core value sets (`core/enums.py`)

`SEVERITY_CHOICES` 1..4 (`Low/Medium/High/Critical`) · `TLP_CHOICES` 0..4
(`White/Green/Amber/Red/Unknown`) · `PAP_CHOICES` 0..3 · `TASK_STATUS_CHOICES`
(`Waiting/InProgress/Completed/Cancel`) · `AUTOMATION_RUN_STATUS_CHOICES`
(`Pending/Running/Success/Failed`) · `CASE_STAGES` (`New/InProgress/Closed`) · `ALERT_STAGES`
(`New/InProgress/Closed/Imported`) · `CUSTOM_FIELD_TYPES` · `API_KEY_SCOPES` (`read/readwrite`).
Builders `in_range(...)`/`in_values(...)` emit the CheckConstraints quoted above.

## Evidence

Conformance pins: `tests/conformance/test_phase3_schema.py` / `test_enum_contracts.py` /
`test_indexes.py`; behaviour pins (numbering, hashing, status semantics): `tests/conformance/
test_case_numbering.py`, `test_observable_hashing.py`, `test_mutation_standard.py`,
`test_seed_migrations.py`. Deviations register: [`deviations.md`](./deviations.md). PostgreSQL-only
semantics (partial index, sequences, CHECK): plan §12 R4/R10 and `docs/planning/VERIFY-2026-10-07-phase10.md`.
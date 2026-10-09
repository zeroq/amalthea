# Amalthea — Automation & Domain Events (implemented)

Module D (AGENTS.md): a case change produces a **domain event**, which dispatches **playbooks**, each
run appends its result back to the case ledger. Source: `core/events.py`, `automation/registry.py`,
`automation/dispatcher.py`, `automation/executor.py`, `automation/tasks.py`.

## Trigger surface

`TRIGGER_EVENTS` (`automation/dispatcher.py`) — the four event names a `Playbook.trigger_event` is
expected to hold:

| event | domain event (`core/events.py`) | emitted by |
|---|---|---|
| `observable.created` | `ObservableCreated(observable_id, case_id)` | `registry.observable_saved` (post_save, `created` only, once per already-linked case) and `registry.dispatch_observable_linked` (explicit call from the link writer, since the `CaseObservable` row post-dates `save()`) |
| `alert.ingested` | `AlertIngested(alert_id, source_id)` | `registry.alert_saved` (post_save, `created` only) |
| `case.status_changed` | `CaseStatusChanged(case_id, old_status, new_status)` | `registry.case_saved` — fires only on a real **stage** transition: `pre_save` snapshots the old `status__stage`, `post_save` fires when it differs and the row was not created |
| `task.completed` | `TaskCompleted(task_id, case_id)` | `registry.task_saved` — fires only on a real transition **into** `Completed`: `pre_save` snapshots the old status, `post_save` emits when `status` transitions *into* `"Completed"` (not on create, not on re-save of an already-completed task) |

`Playbook.trigger_event` is a plain `CharField(100)` — it has **no `choices` and no CHECK
constraint** (`automation/models.py`); the four names above are the convention, not a DB-enforced
set. Unknown event names are inert by construction (`_event_name` maps only the four known event
classes).

## Authoring surface (T2/Phase P2)

`Playbook` is a first-class model (`automation/models.py`). The authoring API is an **Amalthea
extension** (deviation **P12-2** — TheHive 5 / `thehive4py` 2.1.0 expose no playbook route).

| Path | Methods | View |
|---|---|---|
| `playbook`, `playbook/` | GET, POST | `playbook_collection` |
| `playbook/_meta` | GET | `playbook_meta` |
| `playbook/<idOrName>` | GET, PATCH, DELETE | `playbook_detail` |
| `playbook/<idOrName>/run` | POST | `playbook_run` |

**Write-time validation** (`automation.playbooks.validate_config`, mirrors `executor.execute`):
- `action` ∈ `{"http","python"}`.
- `http`: `url` required; `method` ∈ standard verbs; `headers` string→string; `timeoutSeconds` >0 ≤ 30s.
- `python`: `action_path` must be a key in `automation.executor.registered_actions()`.
- Unknown action ⇒ 400. Validation happens at write time so a bad playbook never becomes a `Failed` run.

`GET /playbook/_meta` returns the live vocabulary (AC6.12-P2-c):
```json
{
  "triggerEvents": ["observable.created","alert.ingested","case.status_changed","task.completed"],
  "actions": [
    {"action":"http","methods":[...],"required":["url"],"maxTimeoutSeconds":30},
    {"action":"python","registeredPaths":["amalthea.automation.executor.enrichment_probe",...]}
  ],
  "actionNames":["http","python"]
}
```

`POST /playbook/<idOrName>/run` body `{"case"?:idOrNumber,"observable"?:id}` with at least one required.
Returns 202 + run JSON (`triggeredBy: "manual"`); the run uses a non-deduping key `manual:<uuid4>`,
runs the same worker, and its result lands in `output_log` **and** the case timeline (AC6.12-P2-a).

## Dispatch (`automation/dispatcher.py::dispatch`)

`@transaction.atomic`; on one event:

1. `event_name = _event_name(event)` — maps the event class to one of the four names, else raises.
2. `subject = _idempotent_subject(event)` — `ObservableCreated` yields `"{observable_id}:{case_id}"`
   (required: an observable shared across cases must fire once per *case*); every other event yields
   its subject id (`_subject_id` checks `observable_id`, `alert_id`, `case_id`, `task_id` in order).
3. `playbooks = Playbook.objects.filter(trigger_event=event_name, is_active=True)`.
4. per playbook, `AutomationRun.objects.get_or_create(idempotency_key=key, defaults={…})`:
   - `idempotency_key(playbook_name, event_name, subject)` =
     `f"{event_name}:{playbook_name}:{sha256(f'{playbook}\x1f{event_name}\x1f{subject}').hexdigest()[:32]}"`
     — the key is derived from the event and subject only, never a timestamp, so a replayed
     trigger/retried task/duplicated signal collapses onto one row (AC6.5).
   - defaults stamp `case_id`, `playbook`, the immutable `playbook_name` snapshot (survives a
     rename — REVIEW M14), `trigger_event`, `status="Pending"`, `triggered_by_observable_id`.
   - `was_created` false ⇒ the run already exists; skipped, not raced.
5. after the loop, `transaction.on_commit(partial(execute_run.delay, str(run.id)))` per created run
   (AC6.6: nothing is queued if the producing transaction rolls back; `run.id` is bound, not
   captured, so N playbooks queue N distinct calls).

## Worker (`automation/tasks.py::execute_run`)

`@shared_task(bind=True, max_retries=3, default_retry_delay=5)` — **re-entrant**: a redelivered
message re-reads the run and returns `skipped: "already terminal"` if `status ∈ {Success, Failed}`,
which is what makes Celery's at-least-once delivery safe without a lock.

- `AutomationRun.DoesNotExist` ⇒ `self.retry` (the run may still be mid-commit) — only *missing*
  rows retry.
- Otherwise: `status="Running"`, `started_at = started_at or now()`, `celery_task_id`,
  `save(update_fields=[…])`.
- `execute(run.playbook.config, _interpolation_values(run))` in a try:
  - `UnsafeURLError` ⇒ `Failed` with `"blocked by the SSRF guard: …"` (policy outcome — **not**
    retried).
  - `ActionError` ⇒ `Failed` with its message.
  - any other `Exception` ⇒ `Failed` with `"{type}: {msg}"`.
- `record_result(run, result, subject_label=…)` inside a nested `transaction.atomic()`.

### Interpolation names (`_interpolation_values`)

Deliberately narrow — an action's templates may reference only:

`run_id`, `playbook`, and (when triggered by an observable) `observable` (`normalized_data`),
`observable_type` (`data_type.name`); and (when the run has a case) `case` (`case.number`),
`case_id`.

**`raw_payload` is never exposed** — it is adversary-controlled telemetry and a playbook template is
an interpolation sink; handing it over would make every outbound request carry attacker-chosen
content. `_subject_label` names the observable for the ledger (else the case).

## Executor (`automation/executor.py::execute`)

`config["action"]` must be one of exactly two:

- `"http"` → `run_http_action(config, values)`: one outbound request to `config["url"]` with the
  interpolation values; redirects followed manually and capped (`MAX_REDIRECTS = 3`), body truncated
  to `MAX_OUTPUT_BYTES = 64 KiB` with a `[truncated at …]` marker.
- `"python"` → `run_python_action(config, values)`: `config["action_path"]` looked up in
  `_ACTIONS` (populated by `register_action(path, fn)`); an unregistered path raises `ActionError`
  listing the registered paths. **Nothing from `config` is imported or evaluated** — a playbook row
  names a path, it does not supply code.

Any other action name ⇒ `ActionError` (`"unknown action … expected 'http' or 'python'"`).

### SSRF safety

`ALLOWED_SCHEMES = {"http", "https"}`; `assert_safe_url(url)` re-checks after every interpolation and
**on every redirect hop** (`MAX_REDIRECTS`), `DEFAULT_TIMEOUT_SECONDS = 10.0`. The docstring is
explicit about why the boundary exists: an `fqdn` observable holding `169.254.169.254` must not make
the worker read the cloud metadata service. A shipped no-network action
`amalthea.automation.executor.enrichment_probe` is registered for offline tests.

## Feedback loop (`record_result`)

`AutomationRun` is stamped terminal (`Success`/`Failed`, `output_log`, `error`, `finished_at`) and,
when the run still **has** a case (`SET_NULL` may have cleared it — REVIEW M14; a case-less run
raises `ValueError`), appends a ledger event via
`cases.ledger.append_timeline_event(..., kind="automation-run")`:

- `title`: `f"Automation {status.lower()}: {label}"` (`label` = subject label or `playbook_name`)
- `description`: `(output_log or error)[:4000]`
- `metadata`: `{"runId", "playbook", "triggerEvent", "status"}`

`append_timeline_event` is the single ledger writer and its on-commit publisher is what surfaces the
result live (see [`realtime.md`](./realtime.md)).

## Evidence

`tests/conformance/test_mvp_loop_automation.py` (ingest→escalate→extract→automate→ledger),
`test_automation_executor.py` (SSRF/schemes, registered-action lookup, truncation, interpolation
names), `test_indexes.py` (`ar_pending_idx` is a **Plan** index). Deviation register:
[`deviations.md`](./deviations.md) — P12-1 (task.completed registered-but-unemitted) and P10-4
(alert-observable links do not dispatch).
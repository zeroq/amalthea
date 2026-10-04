# ADR-002: TheHive API Compatibility Strategy

Date: 2026-10-03
Status: Accepted
Deciders: Project lead
Supersedes/Extends: ADR-001-backend-framework
Reference spec: TheHive **v5.8.0** OpenAPI 3.1 (`https://docs.strangebee.com/thehive/api-docs/docs.yaml`, retrieved 2026-10-03)

## Context

TheHive is the de-facto standard for security case management. Thousands of SOCs run it, and a
large ecosystem already speaks its API: the official **TheHive4py** client, TheHive Frontend,
Cortex connectors, MISP modules, and countless internal scripts.

Amalthea deliberately diverges from TheHive in *capability* (orchestration, cross-case observable
forensics, event-driven automation). It should not diverge in *contract*. If an analyst or an
existing integration can point Amalthea at a TheHive-shaped endpoint and have it work, we get:

- Zero-cost migration in both directions (TheHive → Amalthea, Amalthea → TheHive).
- Reuse of TheHive's battle-tested API design instead of inventing our own.
- Compatibility with existing runbooks, dashboards and glue code.
- A familiar object model, which matters because our users are IR practitioners, not web developers.

TheHive's API is, however, **large**: 203 paths and 342 schemas in v5.8.0 alone. Naively targeting
"100% parity" would stall the MVP indefinitely. So we must decide *how* to be compatible.

## Decision

Amalthea exposes a **TheHive-compatible API under `/api/v1/`**, matching TheHive 5.8.0 exactly for
the entities in scope — paths, methods, field names, types, enum values, status codes and error
envelope. Amalthea's differentiating features are added as **strictly additive extensions** on
separate paths that cannot collide with TheHive's namespace.

### D1. Compatibility means: the *external integration surface*, not the UI

TheHive's 203 paths are mostly there to serve TheHive's own Angular frontend. Reimplementing a UI's
read models, computed display fields and internal helper routes would be a large, low-value effort
that would also fight our own UI.

**We are compatible with the endpoints an external tool actually calls, and with the data those
endpoints accept and return.** Concretely, the compatibility surface is:

1. **Authentication** — `Authorization: Bearer <api_key>`, HTTP Basic, `POST /api/v1/login`,
   `GET|POST /api/v1/logout`.
2. **Entity lifecycle** — create / read / update / delete for `case`, `alert`, `observable`, `task`,
   `customField`, `customEvent`, `page`, `comment`, `tag`.
3. **Relationship sub-resources** — the `POST` paths that attach children to a parent:
   `/case/{caseId}/observable`, `/alert/{alertId}/observable`, `/case/{caseId}/task`,
   `/case/{caseId}/customEvent`, `/case/{caseId}/comment`, `/case/{caseId}/page`.
4. **Escalation / merge** — `POST /api/v1/alert/{alertId}/merge/{caseId}` and
   `POST /api/v1/alert/{alertId}/import/{caseId}`. This is the single most-used integration call in
   TheHive deployments.
5. **Attachment upload/download** — external analyzers (Cortex and equivalents) fetch observables
   and case artifacts through these paths; without them no enrichment tool works.
6. **Search** — `POST /api/v1/query`.
7. **Discovery/metadata** — `GET /api/v1/user/current`, `GET /api/v1/customField`,
   `GET /api/v1/observable/type`, status listings, `GET /api/v1/describe/{model}`, `GET /api/v1/export`,
   `GET /api/v1/flow`.

Within that surface, tiers govern **sequencing**, not whether we commit:

| Tier | Scope | Target |
|------|-------|--------|
| **T1 — Core integration** | auth; `case`, `alert`, `observable`, `task` CRUD; relationship sub-resources; alert→case merge; `customEvent` + timeline; `customField`; error envelope; enum + format contract | **MVP** |
| **T2 — Extended integration** | `POST /api/v1/query` DSL; attachments; `page`/`comment`; `tag`; `shares`; `observable/type` CRUD; case/alert status CRUD; `user`; bulk endpoints (`PATCH /case/_bulk`, `POST /case/_merge/{ids}`); `describe`; `export`; `flow`; case templates; taxonomy; procedures/TTP | Post-MVP, committed |
| **T3 — Divergent** | `/api/v1/connector/cortex/*` (analyzer/responder orchestration), Functions API (TheHive Platinum), dashboard/report layout, `?name=`-suffixed query variants | **Not implemented** — see D12 |

### D2. Field naming follows TheHive exactly, not Django conventions

TheHive's entity envelope is not optional to copy — clients key off it:

- `_id` (string), `_type` (`"Case"`/`"Alert"`/`"Observable"`/`"Task"`/`"CustomEvent"`)
- `_createdBy`, `_createdAt`, `_updatedBy`, `_updatedAt` — **Unix epoch milliseconds (integer)**
- `severity` + `severityLabel`, `tlp` + `tlpLabel`, `pap` + `papLabel`
- `customFields` returned as an **array** of `OutputCustomFieldValue`, accepted as either a plain
  object or an ordered array on write

Note the asymmetry (object in, array out) — that is TheHive's actual behaviour and clients rely on it.

The `_`-prefixed envelope and the numeric-enum + label pairs are cheap and appear in tool output, so
we implement them. **Fields that exist only to drive TheHive's UI are not required**: `userPermissions`,
per-case `access` detail, `extraData` sub-documents, and the `timeToDetect`/`timeToTriage`/
`timeToQualify`/`timeToAcknowledge`/`timeToResolve`/`handlingDuration` KPI family may be omitted or
returned as `null` in T1. We will accept them on input and ignore them. The one exception is
`extraData: ["total"]` on the query endpoint, because `X-Total` is documented pagination behaviour
that scripted clients actually branch on (D6).

### D3. Identifiers: UUID internally, opaque string externally

AGENTS.md §3 mandates UUID primary keys. TheHive's own `_id` is a string (rendered `~98112`).
These are compatible: UUIDs are strings, and clients treat `_id` as opaque.

- Store UUID PKs internally (AGENTS.md satisfied).
- Serialize `_id` as the plain UUID string. We deliberately do **not** mimic TheHive's `~` numeric
  prefix — it is an internal storage artifact, and no client parses it.
- Path params named `idOrName`/`{caseId}` accept **either** the UUID or the human case `number`.

### D4. Enums are TheHive's, and statuses become entities

| Concept | TheHive 5.8.0 | AGENTS.md draft | Resolution |
|---|---|---|---|
| Severity | integer `1..4` (Low/Medium/High/Critical) | unspecified | **Adopt TheHive.** Map to labels for display. |
| TLP / PAP | `tlp 0..4`, `pap 0..3` | unspecified | **Adopt TheHive.** |
| Case status | free-text name + derived `stage` ∈ `New`/`InProgress`/`Closed` | fixed `Open/Investigating/Containment/Closed` | **Adopt a `CaseStatus` entity** (`value`, `stage`, `order`, `description`). Seed with AGENTS.md's names mapped onto stages. |
| Alert status | free-text name + `stage` ∈ `New`/`InProgress`/`Closed`/`Imported` | fixed `New/Triaged/Dismissed` | **Adopt an `AlertStatus` entity.** Seed `New`/`Triaged`/`Dismissed` mapped onto stages. |
| Task status | `Waiting` \| `InProgress` \| `Completed` \| `Cancel` | `Todo/InProgress/Done` | **Adopt TheHive's.** AGENTS.md's values are renamed; semantics preserved. |
| Observable type | `dataType` → reference to an `ObservableType` entity (user-definable) | `type` ∈ {IP/Domain/Hash/User} | **Adopt TheHive.** Seed the standard set; keep the type table open so users can add e.g. `ja4-fingerprint`. |

Statuses-as-entities is the single most important structural consequence. It is also strictly better
than AGENTS.md's fixed enums: a SOC that wants a `Contained` status can add one without a schema
migration, and `stage` still gives us a stable 3-bucket grouping for metrics and automation triggers.

### D5. Observables are global entities, not case children

AGENTS.md §3 draws `Observable` with a `case_id` FK. But AGENTS.md §2 Module C requires that an IP
seen in 4 cases links those cases — which is *impossible* if the observable row is owned by one case.
The two halves of the spec are only consistent if observables are global and deduplicated, attached
to cases and alerts through link tables. That is exactly what TheHive does.

**Decision:** `Observable` is a global, deduplicated entity (`dataType` + normalized `data`, unique
together). `CaseObservable` and `AlertObservable` are explicit link tables carrying per-link context
(added-by, tags, is-IOC flag). This *satisfies* AGENTS.md Module C rather than deviating from it.

### D6. Listing happens through `POST /api/v1/query`, not REST collections

There is no `GET /api/v1/case`, `GET /api/v1/alert`, `GET /api/v1/observable` or `GET /api/v1/task`
in TheHive. Every list/search operation is a single endpoint taking a JSON query document:

```json
[
  {"_name": "listCase"},
  {"_eq": {"_field": "severity", "_value": 3}, "_name": "filter"},
  {"_fields": [{"_createdAt": "desc"}], "_name": "sort"},
  {"from": 0, "to": 10, "extraData": ["taskStats", "total"], "_name": "page"}
]
```

The response is a **bare JSON array** (not an envelope), with the total count in an `X-Total` header.

This is a real cost: a query engine with ~20 filter operators (`_and`, `_or`, `_not`, `_eq`, `_gt`,
`_between`, `_has`, `_like`, `_match`, …) is a meaningful subsystem. We accept it, because it is
what compatibility means here. It is T2, and the MVP may ship a conformant subset (`listCase`,
`listAlert`, `listObservable`, `getCase`, `_eq`/`_and`/`_or`, `_sort`, `_page`) while documenting the
gap. Conventional REST list endpoints (`GET /api/v1/case`) are added **as an extension** for
convenience — additive, and explicitly not part of the compatibility contract.

### D7. Errors

Uniform envelope, `{"type": ..., "message": ...}`, both keys required:

| Status | `type` |
|---|---|
| 400 | `BadRequest` |
| 401 | `AuthenticationError` |
| 403 | `AuthorizationError` |
| 404 | `NotFoundError` |
| 500 | `GenericError` |

Validation failures return **400** with `type: "BadRequest"` and a `fields` map — *not* DRF's
default 400 shape. DRF's default exception handler must be replaced project-wide.

### D8. Auth

`Authorization: Bearer <api_key>` (primary) and HTTP Basic (secondary), exactly as TheHive.
`POST /api/v1/login` is public and returns a session cookie + `OutputUser`.
`GET|POST /api/v1/logout` are public.

Implementation: API keys are **not** Django auth tokens. A dedicated `ApiKey` model (hashed at rest,
prefix-indexed for lookup, carrying a read/write scope) is resolved by a DRF authentication class.
Session auth backs the web UI. WebSocket handshakes reuse the session, never a bearer token in the URL.

### D9. Extension namespace

Amalthea-only surface, deliberately outside TheHive's namespace:

| Path | Purpose |
|---|---|
| `POST /api/v1/alerts/webhook/{source_id}` | Gossamer feed — arbitrary JSON ingest (AGENTS.md Module A) |
| `GET /api/v1/alert/{id}/raw` | TheHive has no raw-payload accessor |
| `/api/v1/automation/…` | Playbook definitions, `AutomationRun` records, execution logs |
| `GET /api/v1/case/{id}/observable/_graph` | Cross-case observable neighbourhood (Module C) |
| `GET /api/v1/case/{id}/similar/{ref}/observables` | Already TheHive-compatible; semantics differ, see D10 |

Automation results are **not** an extension-only concept: per AGENTS.md Module D they are written
back into the case timeline as `CustomEvent` rows, so they are visible to any TheHive client.

### D10. Deliberate semantic divergences (documented, not accidental)

1. **Observable dedup scope.** TheHive dedups per-org with an explicit `ignoreSimilarity` escape
   hatch. We dedup globally and attach to N cases, which is what Module C requires.
2. **Automatic correlation.** TheHive relies on user-run "similar observables" queries. We
   auto-correlate (e.g. same destination IP within a 10-minute window). This is a *superset* of
   behaviour on a TheHive-shaped endpoint, and is opt-out per source.
3. **Severity floor.** TheHive accepts `severity` 1–4; AGENTS.md's ingestion pipeline may infer a
   higher severity from enrichment. Inference never silently downgrades an analyst-set severity.

### D11. Internal freedom, wire-level tolerance

**Internal freedom.** "Compatible" constrains the wire, not the backend. Internally we are free to
use clean Django idioms — real `ForeignKey`s, `DateTimeField`s, ISO-8601 on our native API, service
objects, normalized joins, `select_related`. The compatibility layer is a **translation boundary**
and nothing leaks past it:

```
External tool ──▶ TheHive-compatible API  ──▶ mappers ──▶ domain services ──▶ Django ORM/Postgres
Amalthea UI   ──▶ Native REST API         ──▶ (same services, no TheHive shapes)
```

Two serialization families exist per entity (`XSerializer` native, `TheHiveXSerializer` wire), and
TheHive field-name string literals are confined to the mapper modules. Because of this, we do not
need to mirror TheHive's *storage* choices either — our `Alert.case_id` FK, our `Observable` global
uniqueness, and our `AutomationRun` table are all internal facts invisible to clients.

**Wire-level tolerance.** TheHive is strict in places that break real ingestion tools. Where strictness
buys us nothing, we are deliberately more forgiving, because a rejected alert means a lost detection:

| Situation | TheHive | Amalthea |
|---|---|---|
| `status` name does not exist | 400 `BadRequest` | **Auto-create** the status with a default stage (`New` for alert, `New` for case), log it, proceed |
| `customFields` name not defined | 400 | Store in an "undeclared" bucket; surfaced, never fatal |
| `dataType` not a defined observable type | 400 | **Auto-create** the type (non-attachment, case-insensitive) and proceed |
| `assignee` login unknown | 400 | Accept, leave unassigned, record the attempted login |
| Unknown top-level field | varies | **Ignore** rather than reject |
| Epoch-ms sent as ISO-8601 string, or vice versa | rejected | **Accept both** on input; always emit epoch-ms |

Every tolerance is recorded on the entity (an ingestion-warning list surfaced to analysts) so that
leniency never becomes silent data loss. Strictness is kept where it is a security boundary:
authentication, authorisation, ownership and upload path handling.

### D12. Explicitly out of scope (TheHive UI surface)

These exist to serve TheHive's frontend or its commercial tier. We neither implement nor emulate
them, and a TheHive frontend pointed at Amalthea is explicitly unsupported:

- Frontend-internal query variants (`POST /api/v1/query?name=alert-similar-cases`, etc.). We accept
  and ignore a `name` parameter so old clients don't 400, but we do not implement its behaviour.
- Dashboards, case-report templates and report upload/attachments.
- SSO/authentication-policy endpoints, `GET|POST /api/v1/config/user`, password-policy endpoints.
- Organisation avatars and org-to-org link management (the *access* model is retained, the
  multi-tenant administration UI is not).
- `POST /api/v1/pattern/import/attack` and pattern management.
- TheHive Functions API (Platinum). This is the closest overlap with Amalthea's purpose, and it is
  where we intentionally compete rather than clone.

**Consequence:** Amalthea's own UI consumes our *native* API, never the compatibility layer. The
compatibility layer exists for third-party tools, and is free to be a slightly lossy, slower,
TheHive-shaped skin over a cleaner internal core.


## Consequences

**Positive**
- A real, verifiable compatibility target instead of a bespoke API.
- Migration and ecosystem reuse for free.
- Strict compatibility forced better modelling (statuses as entities, global observables), which
  improves Amalthea on its own terms.

**Negative / Tradeoffs**
- We inherit TheHive's ergonomics: `_name`/`_field`/`_value` query documents are unidiomatic; `_id`
  underscore-prefixed fields are un-Pythonic; epoch-ms integers are unfriendly in templates and logs.
  We mitigate with a **native** serialization layer (`internal ↔ thehive` mappers) so the Django
  codebase never sees wire format, and keep a clean REST API alongside for our own UI.
- The query engine is real work and is the largest single T2 item.
- Two serializations mean two things to test. Mitigated by conformance tests asserting both against
  the same fixtures.
- T1 conformance is a hard external constraint on model evolution; changing a field name is now a
  breaking change. Accepted deliberately.

## Implementation Notes

- **Conformance suite is the deliverable, not the code.** Contract tests in
  `tests/conformance/` replay recorded TheHive request/response pairs. A golden-file corpus of
  TheHive 5.8.0 responses lives in `tests/fixtures/thehive/` so drift is caught in CI.
- Two serializer families per entity: `XSerializer` (native, DRF-idiomatic, ISO-8601 timestamps,
  used by our own UI and the extension endpoints) and `TheHiveXSerializer` (wire format). One
  mapper module owns the translation; no field-name literals outside it.
- Reference client for interop testing: `TheHive4py`. An interop test runs TheHive4py against a
  running Amalthea and asserts it can list cases, create an alert, and merge it into a case.
- `_id`-shaped output means no Django `HyperlinkedIdentityField`; `_id` is a plain
  `serializers.CharField(read_only=True)`.
- Timestamps: store `timestamptz` in Postgres; convert to epoch-ms on the TheHive wire only.

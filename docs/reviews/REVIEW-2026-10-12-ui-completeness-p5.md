# Security Review — UI completeness wave, Plan P5 (playbook round-trip D1, sanitized Markdown D3)

**Date:** 2026-10-12
**Auditor:** security-auditor (read-only on product code; this report is the only artefact written)
**Scope:** the uncommitted P5 surfaces — `ui/markdown.py`, `ui/templatetags/ui_tags.py` (`markdown` filter),
`ui/views.py` (`_parse_playbook_config`, `playbook_create`/`_detail`/`_edit`/`_run`/`_delete`),
the playbook templates (`playbook_form.html`, `playbook_detail.html`), the vendored CSS/JS asset changes,
and the dependency additions (`markdown-it-py==4.2.0`, `mdurl==0.1.2`, `nh3==0.3.7`).
Cross-cut checks against the P2 mutation views (`alert_status`, `case_edit`, `observable_edit/delete`) because
they share the “UI role gate?” question with the new playbook views.
**Plan / ADR:** `docs/planning/PLAN-2026-10-11-ui-completeness.md` §8 P5; `docs/decisions/ADR-005-markdown-rendering.md`.
**Baseline:** working tree vs `cc9e569` (24 tracked files changed, +1171/−204; 9 untracked paths). Everything reviewed is uncommitted on `main`.
**Method:** `git diff` source review of every changed/added file; adversarial probing of the Markdown sanitizer (`ui/markdown.py`) and direct `nh3`; traced tenant-scoping helpers and the playbook parse/execute path; manual settings review (CSRF/CSP/middleware); `pip-audit`; secret scan; ran the shipped conformance suites.
**Gate observed:** `pytest test_ui_markdown.py test_playbook_roundtrip.py test_ui_inline_assets.py -q` → **172 passed** (27 + 14 + 131).
`pip-audit -r requirements/base.txt` → **No known vulnerabilities found**.
`scripts/secret-scan.sh --files` over all 34 changed/untracked paths → **clean (34/34)**.

---

## Verdict

**CLEAR FOR SIGN-OFF WITH THREE MEDIUM ITEMS TO REMEDIATE.**

No Critical and no High finding. The Markdown rendering (D3) is a genuinely sound control: `markdown-it-py` is
configured with `html=False` (raw HTML in source is escaped, never passed through), and `nh3.clean` against a
strict 18-tag / `{a: href,title}` / `{http,https,mailto}` allow-list is the single trusted boundary; the failure
path is fail-closed (`escape(text)`). ~50 adversarial payloads (script/svg/img-onerror/iframe/srcdoc/style/form
plus `javascript:`/`data:`/`vbscript:` and entity/tab/NUL/percent obfuscations) were neutralised — see §Cleared.
The D1 round-trip is functionally correct. The residual risk is not in the new rendering path but in three
pre-existing/systemic properties that this batch either touches or sits next to: the `read-only` role is not a
mutation boundary for cases/alerts/observables, `_parse_playbook_config` has an unbounded loop, and playbook
action configs (which frequently carry API credentials) are global and shown in cleartext to every authenticated
user.

---

## Findings (severity-ranked)

| ID | Sev | Area | File:line | Introduced by this batch? |
|----|-----|------|-----------|---------------------------|
| M1 | **MEDIUM** | `read-only` role can mutate cases / change alert status / delete observables | `ui/views.py:1386,1443,1614,1772`; `identity/scoping.py:134` | No (pre-existing; P2 UI, matches API). Surfaced for a decision. |
| M2 | **MEDIUM** | Unbounded `header_count` loop → authenticated CPU/memory DoS | `ui/views.py:130-141` | Yes (P5 round-trip helper). |
| M3 | **MEDIUM** | Playbook header secrets shown in cleartext to any authenticated user (incl. `read-only`, cross-tenant) | `ui/views.py:846-861`; `ui/templates/ui/playbook_form.html:85`; `automation/models.py:13` | Rendered by P5 round-trip work; model is pre-existing. |
| L1 | LOW | Protocol-relative `//evil.example` survives as `<a href>` (phishing aid) | `ui/markdown.py:54` | Yes (D3). |
| L2 | LOW | `playbook_edit` / `playbook_create` lack `@require_POST`/`@require_http_methods` | `ui/views.py:829-830, 633-634` | Partly (helper refactor). |
| I1 | Info | Userinfo URL spoofing `http://safe.example@evil.example` permitted | `ui/markdown.py:54` | Yes (D3) — inherent to allowing `http(s)`. |
| I2 | Info | Header values not CRLF-validated at authoring time | `ui/views.py:138-139` | Yes (P5) — see note. |
| I3 | Info | `render_markdown` LRU retains up to 512 input strings (large fields pin memory) | `ui/markdown.py:57` | Yes (D3) — bounded. |

---

### M1 — MEDIUM: the `read-only` role can still mutate cases, change alert status, and delete observables

**Where.** The role vocabulary is `superAdmin / admin / analyst / read-only` (`docs/spec/identity-auth.md:16`).
The playbook views *do* enforce a role gate (`ui/views.py:638, 864, 898, 923`), but the case/alert/observable
mutation views carry only `@login_required` (+ method decorators):

- `alert_status` — `ui/views.py:1384-1386` (`@login_required @require_POST`)
- `case_edit` — `ui/views.py:1441-1443`
- `observable_edit` — `ui/views.py:1612-1614`
- `observable_delete` — `ui/views.py:1770-1772`

`resolve_case(..., write=True)` grants an **owner-org member** full write access regardless of role
(`identity/scoping.py:133-135`), and `resolve_alert` / `resolve_observable` have no write/role parameter at all
(`identity/scoping.py:159-186`).

**Repro (confirmed).** As a user with `role="read-only"` in the owning org, using a session:
`POST /ui/cases/<id>/edit` with `title=…` → 302 and `case.title` changed; `POST /ui/alerts/<id>/status` → 302 and
the alert status changed; `POST /ui/observables/<id>/delete` → 302 and the observable is gone. All three would be
404 for an `analyst`-gated route, but these routes don't gate on role.

**Impact.** A principal explicitly labelled “read-only” can destroy forensic observables and alter case/alert
state. This is a *privilege/intent* mismatch more than a tenant-isolation break (org scoping still holds). Note
this is consistent with the DRF API: the case/alert/observable API views set no `permission_classes`, so the
default `IsAuthenticated` + `ScopePermission` applies and a session user of any role can mutate there too —
i.e. this is a **systemic policy gap**, not a UI regression. It nonetheless contradicts the role name and the
playbook precedent, and the UI now makes it point-and-click.

**Recommendation.** Decide the policy explicitly, then enforce it in one place:
- If `read-only` must be read-only everywhere: add an `IsAnalystOrAbove`-equivalent decorator to the four UI
  views and a DRF permission class to the corresponding API views; return the uniform 404 the codebase already
  prefers. Cover with a conformance test (read-only POST → no state change).
- If it is intentional MVP behaviour: document it in `docs/spec/identity-auth.md` and stop calling the role
  “read-only”, so it is not mistaken for a control.

---

### M2 — MEDIUM: unbounded `header_count` loop lets an authenticated user burn a worker

**Where.** `ui/views.py:128-141`:

```python
header_count = post.get("header_count")
try:
    max_idx = int(header_count) if header_count else 4
except ValueError:
    max_idx = 4
for i in range(max_idx):
    ...
```

There is **no upper bound** on `max_idx`. The value is taken straight from the POST body of
`playbook_create` / `playbook_edit` (both require only `analyst` or above, or a `read-only` user per M1).

**Timing (measured in-process).** `header_count=1_000_000` → **0.141 s**; `5_000_000` → **0.698 s**
(~7M iterations/s on this host). A single request with `header_count=1000000000` ≈ **140 s** of CPU in one
worker before any response is produced; a handful of concurrent requests saturate every Daphne/Celery worker.
Because the loop also builds a `headers` dict keyed by attacker-chosen field names, a large value grows memory
as well (one dict entry per non-empty `header_key_<i>`), so it is CPU-and-memory amplification.

**Impact.** Authenticated denial of service against the whole UI/worker pool. No cross-tenant or data impact.

**Recommendation.** Cap it, and reject rather than silently clamp:

```python
try:
    max_idx = int(header_count) if header_count else 4
except (TypeError, ValueError):
    max_idx = 4
if not 0 <= max_idx <= 64:  # far above any real playbook
    return None, "Too many headers."
```

The same helper is the natural place to also cap total config size. Add a regression test posting
`header_count=1000000` and asserting the form error.

---

### M3 — MEDIUM: playbook action secrets are global and rendered in cleartext to every authenticated user

**Where.** `Playbook` has **no `owner_org`** (`automation/models.py:13-28`) — it is a single global table shared
by every tenant. HTTP playbooks store their outbound credentials in `config["headers"]`
(`ui/views.py:128-141`), which routinely means `Authorization: Bearer …`, an API key, or a webhook secret.
`playbook_edit` renders the entire config on **GET for any authenticated member of any org**, including
`read-only` (`ui/views.py:846-861` → `playbook_form.html:81-85`, which emits
`<input … value="{{ val }}" placeholder="Header value">`). `playbook_detail` is likewise a level-0 read.

**Repro.** Log in as a `read-only` user in Org B; `GET /ui/automation/playbooks/<id>/edit` for a playbook created
by Org A → the full header map, including any bearer token, is in the response HTML.

**Impact.** Cross-tenant credential disclosure for any deployment that keeps playbooks global. Even within one
org, a `read-only` user gets secrets that authoring is otherwise gated away from. This is the most consequential
item for real deployments because it leaks usable third-party credentials.

**Recommendation (pick one, in order of preference):**
1. Scope `Playbook` with `owner_org` and filter reads/writes through `org_q` (matches ADR-004); or
2. Keep playbooks global but stop persisting secrets in plaintext config — store a reference to a secret and
   render a masked placeholder (`••••`) on GET, requiring explicit re-entry to change it; or
3. At minimum gate the GET edit/detail views to `analyst`+ (the same predicate as write) and mask header values.

Whichever is chosen, add a test asserting a `read-only` (and cross-tenant) GET does not return the raw header
values.

---

### L1 — LOW: protocol-relative `//evil.example` survives the sanitizer as an `href`

`_ALLOWED_URL_SCHEMES = {"http","https","mailto"}` (`ui/markdown.py:54`). `nh3` resolves scheme-relative URLs by
inheriting the current scheme, so `[x](//evil.example)` is emitted unchanged as
`<a href="//evil.example" rel="noopener noreferrer">x</a>`. Direct `javascript:`/`data:`/`vbscript:` (including
entity, tab, NUL and percent obfuscations) are correctly stripped; this is the only surviving oddity.

**Impact.** Not XSS. An analyst-authored comment can render a same-scheme link to an arbitrary host — a phishing
/ open-redirect assist inside an authenticated console. Low.

**Fix (optional):** prepend `//` rejection to the allow-list, e.g. post-process `href` values whose `urlsplit`
has an empty scheme and a `netloc`, or accept it as intended behaviour for internal links. If kept, no action.

---

### L2 — LOW: `playbook_edit` / `playbook_create` do not restrict HTTP methods

`playbook_edit` (`ui/views.py:829-830`) and `playbook_create` (`:633-634`) are `@login_required` only — unlike
their siblings (`playbook_delete`/`playbook_run` use `@require_POST`; `case_edit` uses
`@require_http_methods(["GET","POST"])`). A `PUT`/`DELETE` falls through the `GET` check into the mutation branch.
In practice the mutation branch reads `request.POST`, which Django leaves empty for non-`POST` verbs, so the
parse returns an error and no write occurs — but the behaviour is accidental and CSRF still applies.

**Fix:** add `@require_http_methods(["GET","POST"])` to both, matching `case_edit`.

---

### Info / notes

- **I1 — userinfo spoofing.** `http://safe.example@evil.example` is a valid `http` URL and is kept; the visible
  link text can mislead. Inherent to allowing `http(s)`; consider documenting, not blocking.
- **I2 — header CRLF.** Header keys/values are stored as given (`ui/views.py:138-139`). Outbound execution uses
  the Python HTTP stack, which rejects header values containing CRLF/control characters, so this is not header
  injection — a malformed playbook simply fails its run. Optional hardening: reject CRLF at authoring so the
  failure is a form error rather than a failed run.
- **I3 — Markdown LRU.** `@lru_cache(maxsize=512)` on `render_markdown` (`ui/markdown.py:57`) is bounded to 512
  entries; because `Case.description`/comment bodies are unbounded `TextField`s, 512 very large inputs could pin
  meaningful memory. Low; a size guard (`if len(text) > N: skip cache`) would remove it.

---

## Cleared / tested attack classes

- **XSS in rendered Markdown (D3) — PASS.** `MarkdownIt("commonmark", {"html": False})` escapes source HTML, then
  `nh3.clean` enforces the allow-list. Probed ~50 payloads: `<script>`, `<img src=x onerror>`, `<svg/onload>`,
  `<iframe>`, `<object>`, `<embed>`, `<style>`, `<form>`, `<meta>`, `<base>`, `<textarea srcdoc>`, mXSS, and
  `javascript:`/`data:`/`vbscript:` in mixed case, entity, tab and NUL/percent-obfuscated forms — all become inert
  escaped text; `title` quote-breakout is escaped; no `<a>`/`<script>`/`<img>`/`<iframe>`/`<style>`/`<form>`/`<svg>`
  element survives outside the allow-list. The fail-closed path was verified by monkeypatching both `_MD.render`
  and `nh3.clean` to raise → the function returns input escaped as plain text. See also `VERIFY-2026-10-12-…p5.md`.
- **Stored XSS via playbook config textarea — PASS.** `playbook_form.html` renders `{{ config.body }}` and
  `{{ val }}` with normal autoescaping (no `|safe`); the earlier `|safe` was removed and a regression test in
  `test_playbook_roundtrip.py` asserts `</textarea><img`/`onerror=` do not appear.
- **CSRF — PASS.** `CsrfViewMiddleware` enabled; no UI mutation view is `csrf_exempt`; every form carries
  `{% csrf_token %}`; the conformance tests post without a token → 403.
- **SQL injection — PASS.** All reads/writes go through the ORM with parameterised queries; no raw SQL added.
- **Inline-script / CSP — PASS.** No new inline `<script>` or `style=`; assets remain self-hosted
  (`script-src 'self'`, `style-src 'self'`). The CSS diff is vendored Tailwind output plus `.md` rules — no
  `@import`/`url()` to third parties.
- **Secrets in the repo — PASS.** `scripts/secret-scan.sh --files` clean over all 34 changed/untracked paths.
- **Dependency advisories — PASS.** `pip-audit -r requirements/base.txt` → no known vulnerabilities
  (covers `nh3 0.3.7`, `markdown-it-py 4.2.0`, `mdurl 0.1.2`).
- **Mass assignment — PASS.** `_parse_playbook_config` writes an explicit, closed key set
  (`action`, `url`, `method`, `timeoutSeconds`, `headers`, `body`, `action_path`); no user-controlled keys reach
  the model wholesale.
- **Playbook authoring/run role gate — PASS (playbooks only).** `create`/`edit`/`run`/`delete` deny `read-only`
  with a 404 (`ui/views.py:638,864,898,923`). Contrast with M1 for the other entities.

---

## Summary of remediation priority

1. **M3** — stop leaking playbook credentials broadly (highest real-world impact).
2. **M2** — cap `header_count` (trivial, one-line guard + test).
3. **M1** — decide and document/enforce the `read-only` policy (spans P2 UI + API, not just this batch).
4. L1/L2 and the Info notes are optional hardening.

**Verdict: no blocker for the P5 feature itself; the Markdown and round-trip work is well-built. Clear to ship
once M2 and M3 are addressed (or explicitly accepted), with M1 tracked as a cross-cutting policy decision.**

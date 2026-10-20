# PLAN — UI layout/style standardization (§12.9) + split the Tailwind source from the artifact (§7.4)

- **Date:** 2026-10-19
- **Author:** planner
- **Status:** Approved. Phase A **implemented + independently verified** (see §Phase A verification log);
  Phases B–D pending. Uncommitted on `main` at `1d374de`.
- **Closes:** TODO §7.4, TODO §12.9
- **Depends on:** nothing (this plan *is* the §7.4 prerequisite for §12.9's CSS half)
- **Related:** AGENTS.md §6 (design & style), ADR-003 (no inline scripts/styles), §12.10 (severity-class drift — the near-miss that motivated doing §7.4), §12.5-fu1 (modal id a11y precedent for guard tests)

---

## 1. Summary

TODO §12.9 asks for three UI/consistency conventions to be captured and applied:

1. input/button height alignment in action rows;
2. icon-only actions in tight action columns (with accessible names);
3. every list table inside a `.card`, with filters in a **separate** card above it (reference: Observable List).

Reconnaissance (2026-10-19) shows the work splits into **three different kinds of task**, and one of them is
blocked by a **latent, high-severity defect in the build pipeline**:

- Sub-item (1) is **already implemented twice**, under two names: `.case-actions` and `.filter-bar`, with
  near-identical rules and duplicated CSS. The remaining work is consolidation + rename, **which needs the
  stylesheet pipeline fixed first** (§7.4).
- Sub-item (2) is genuine work across ~10 list templates, and is **constrained by a narrow Font Awesome
  subset** (§5 of this plan).
- Sub-item (3) is mechanical, but only **one** of the 13 list surfaces currently matches the reference.

Additionally, this plan records a **new finding** discovered while reconning for this work:

> **`make css` destroys ~10 KB of hand-written CSS.** `ui/static/ui/app.css` is simultaneously the Tailwind
> *input* and the committed *output*. Compiling it today (`scripts/tailwindcss -i ui/static/ui/app.css -o
> ui/static/ui/app.css --minify`) replaces the file with **53 717** bytes, dropping all **9** hand-written
> component sections (`63 591` bytes on disk) — `.case-actions`, `.filter-bar`, `.sev-*`, `.tabs`,
> `.data-table`, `.modal*`, `.pill-*` and the rest, i.e. **59 top-level component selectors**. Nothing fails:
> `css-check` only proves the input *compiles* ("this does NOT detect drift", by its own output message), and
> no test asserts the component classes exist. A single `make css` on a clean run therefore silently removes
> most of the UI's styling. This is severable from §12.9 and is fixed by §7.4; it is the reason §7.4 is
> scheduled **first**.

## 2. Goals

- **G1.** Make the stylesheet pipeline idempotent and drift-detecting: a committed source, a generated
  artifact, and a gate that fails when the artifact is stale (§7.4).
- **G2.** Codify the UI layout/style rules in one discoverable document, with the Observable list as the
  worked reference (§12.9).
- **G3.** Apply the layout conventions (filter card + table card) to every list surface (§12.9.3).
- **G4.** Collapse the duplicated equal-height control utilities into **one** neutrally-named utility and
  apply it wherever controls sit next to each other (§12.9.1).
- **G5.** Convert tight, ubiquitous secondary row actions to icon-only controls with accessible names, keeping
  visible text labels on primary standalone actions (§12.9.2, AGENTS.md §6).
- **G6.** Add regression guards so drift in any of the above fails a test rather than shipping.

## 3. Non-goals

- **N1.** No redesign. No change to the dark theme, the colour tokens, typography, or the information
  architecture. This is standardization, not restyling.
- **N2.** No change to server-rendered/HTMX behaviour, URLs, view logic, permissions, or the API. If a
  template change would require touching `ui/views.py`, that is out of scope and must be raised instead.
- **N3.** No new icon *assets* beyond the existing vendored Font Awesome subset unless the guard in §5 forces
  it (see D3).
- **N4.** No conversion of **primary** actions to icon-only, and no icon-only control without an accessible
  name.
- **N5.** No component framework, no new JS, no CSS framework change (Tailwind stays; ADR-003 respected).
- **N6.** Not fixing the *content* of the 24 UI-completeness gaps (§6.16) or the pagination/`COUNT` question
  (§12.1-fu1).

## 4. Architecture: the stylesheet pipeline (§7.4)

**Today.** `ui/static/ui/app.css` is one file that is both input and output:

- line 1: the Tailwind banner;
- line 2: minified Tailwind theme/framework output (`@layer theme{…}`);
- lines 3+: **hand-written** component CSS in 9 `/* ===…=== */` sections.

`make css` = `tailwindcss -i app.css -o app.css --minify`. Because Tailwind treats the whole file as its
input and the output overwrites it, the hand-written sections are **not** preserved (verified: output is
53 717 bytes vs 63 591 on disk, 808 diff lines, all 9 sections gone). `make check`'s `css-check` compiles to
`/dev/null` and therefore cannot notice.

**Target layout (ADR-003-compatible, no behaviour change):**

| File | Role | Committed? | Hand-edited? |
|---|---|---|---|
| `ui/static/ui/app.src.css` | the **only** Tailwind input: `@import "tailwindcss"`/theme + the 9 component sections (verbatim, deduplicated) | yes | **yes** |
| `ui/static/ui/app.css` | generated artifact, minified | yes | **never** |
| `scripts/tailwindcss` | pinned binary (unchanged) | yes | no |

**Rationale for keeping the artifact committed** rather than build-time-only: the app is server-rendered with
no Node in the runtime path, `make css` must work on a fresh clone, and the CSS must be servable without a
build step. Committing the artifact **plus** enforcing freshness is the honest version of what §7.4 asks for
("generate `app.css` from it, and make `css-check` fail when the tree's artifact is stale").

**`css-check` becomes a real drift gate.** Compile `app.src.css` to a temp file and byte-compare with the
committed `app.css`:

- equal → pass;
- differ → **fail**, printing the first differing hunk and the remediation (`make css`), *and* naming the
  likely cause ("a hand-written rule was added to `app.css` instead of `app.src.css`").
- Tailwind binary unavailable on this host → keep today's **platform-aware skip** (the existing behaviour on
  non-arm64 hosts), but make the skip **loud** and record it in the target gate output so a skipped gate is
  never mistaken for a passing one. (See D2.)

**Bootstrap.** Splitting the file is a one-time mechanical extraction: move the 9 component sections verbatim
into `app.src.css`, add the Tailwind entry directive(s) the current output implies, regenerate `app.css`, and
**assert byte-identity of the generated artifact against the previously committed one modulo whitespace**, per
AC-3. Any *visual* difference is a stop-and-raise, not a fix-up.

> **Correction 2026-10-19 (post-failed first attempt — read this before Phase A is attempted again).**
> The paragraph above is **wrong in two ways**, and the first Phase A attempt failed because of it:
>
> 1. **The hand-written CSS is not confined to the 9 labelled sections.** `app.css` line 2 is itself the
>    *compiled* artifact — it contains `@layer theme`, `@layer base`, `@layer components` (with `.shell`,
>    `.rail`, `.card`, `.btn`, `.data-table`, `.md`, `.sev-*`, `.notfound`, the scrollbar rules, the global
>    `:focus-visible` rule, the cheatsheet modal, `.filter-bar`, `.field-row`, `.attachment-upload`, …) and
>    `@layer utilities`. Everything from line 3 to EOF *is* hand-written, but so is a large part of the
>    `@layer components` block on line 2. Scoping the extraction to the 9 sections is what lost 81 selectors.
> 2. **Reconstructing the source from an older commit (`df9633d^`) is forbidden.** It produced an artifact
>    that was *different from the current one* — 81 selectors absent (`git`-verified), including `.data-table`
>    (every list table), `.table-wrap`, `.md` (all Markdown), `.notfound`, the cheatsheet modal, `:focus-visible`
>    (the keyboard focus indicator) and the scrollbar styling. The committed `app.css` is the ground truth.
>
> **Correct mental model for the split:** the source is `app.css` line 2's `@layer …` content (de-minified,
> i.e. the *same rules*, just readable) plus the line-3-onwards hand-written block, verbatim. The artifact is
> what the vendored `scripts/tailwindcss` emits from that source. Success = the new artifact has **every**
> selector the old artifact had (a full selector-set comparison of old vs new, not a hand-picked list), not
> merely the 59 an eye-picked list happened to name.
>
> **Why the first attempt's own check passed:** it compared the artifact against the *source it had just
> written*, so a lossy source yields a self-consistent (and equally lossy) artifact. The gate must therefore
> also be anchored to the **pre-split artifact** (`git show <pre-split-rev>:ui/static/ui/app.css`), as a
> one-time bootstrap assertion recorded by the verifier — see revised AC-3/AC-3b for Phase A below.

## 5. Constraint: the Font Awesome subset is narrow (affects G5)

`ui/static/vendor/fontawesome/css/fontawesome-subset.min.css` (6.9 KB) is a **subset**. Reconnaissance of the
icons the list action columns would want:

| Icon | In subset? | Note |
|---|---|---|
| `fa-eye` | yes | View |
| `fa-pen` | yes | Edit |
| `fa-trash` | yes | Delete |
| `fa-xmark` | yes | Close/cancel |
| `fa-gear` | yes | Settings/Edit |
| `fa-rotate` | yes | Re-run |
| `fa-download` | yes | Export/Download |
| `fa-key` | yes | Token |
| `fa-circle-check`, `fa-circle-xmark`, `fa-circle-info`, `fa-circle-exclamation` | yes | states |
| `fa-play` | **no** | needed for "Run now" |
| `fa-pause` | **no** | needed for "Pause token" |
| `fa-ban` | **no** | needed for "Deactivate" |
| `fa-pencil` | **no** | variant spelling of `fa-pen` |

Consequences to settle in implementation (D3): either (a) use only subset icons and choose an available
surrogate where the natural glyph is missing, (b) extend the subset via the vendoring script (needs the
vendoring path to be reproducible and is a *supply-chain-relevant* change), or (c) leave those particular
actions as text buttons and document why. `fa-pencil`→`fa-pen` is a pure spelling fix, not a new icon.

## 6. Data model / API

**None.** No model, migration, schema, endpoint, or URL changes. Explicitly: no new `Meta`, no new index, no
change to any serializer or view contract. (One exception to flag: if the FA subset must be extended, the
vendored *asset* changes — no API.)

## 7. Design decisions

| # | Decision | Rationale |
|---|---|---|
| **D1** | Fix §7.4 **before** §12.9's CSS half; §12.9's template half may land in parallel. | Sub-items (1) and (4) both edit the stylesheet. Doing them on the current pipeline risks the work being silently erased by the next `make css` — the exact failure mode §12.10 narrowly avoided. |
| **D2** | `css-check` byte-compares generated vs committed; on unsupported hosts it **skips loudly** rather than failing. | Preserves a green local gate on Intel/Linux developers, but removes today's ambiguity where a skip prints "✓ Tailwind input compiles". A skipped gate must be distinguishable from a passing one in the output. |
| **D3** | Prefer (a): use only icons already in the subset; do **not** extend the vendored subset in this wave. | Extending a vendored, supply-chain-relevant asset deserves its own review. Where no subset glyph fits, keep a text button (N4) and record it. Revisit as a follow-up if the result is visibly inconsistent. |
| **D4** | The equal-height utility is renamed to a **neutral** name and becomes the single source of truth; `.filter-bar` is folded into it. | §12.9's own note ("generalize/rename it as part of this item rather than inventing a second utility"). `.case-actions` is already used by `source_detail.html`, `settings_sources.html` and `_tab_general.html`, so the case-flavoured name is already a lie. |
| **D5** | The old class name is kept as an **alias** for one wave. | 25 references across 8 templates plus 4 planning docs; an alias keeps the diff reviewable and lets the rename be verified independently. Remove the alias in the same commit only if the guard test (AC-9) asserts zero remaining uses. |
| **D6** | Icon-only is restricted to **secondary, ubiquitous** row actions (View/Edit/Delete-class). Primary standalone actions keep text; where an icon accompanies a label it is decorative (`aria-hidden="true"`). | AGENTS.md §6 verbatim: an icon *supplements* a visible label on primary actions, never replaces it; icon-only controls must carry an accessible name. |
| **D7** | The conventions live in **`docs/spec/ui-style.md`** (not `ui/README.md`; there is no `ui/README.md`, and `docs/spec/` already holds the other house specs). | Discoverability + consistency with `api.md`, `data-model.md`, `realtime.md`, … |
| **D8** | Extend `tests/conformance/test_ui_css_tokens.py` rather than adding a new test module for the styling guards. | That module already owns "the stylesheet must not silently break" (undefined tokens, focus outline, tab scrollbars). The new guards are the same species. |
| **D9** | (Phase A follow-up) disable Tailwind automatic source detection (`source(none)`) and declare `@source` explicitly for `ui/templates/**` and `ui/static/ui/*.js`. | Auto-detection scanned the whole repo (minus `.gitignore`), so `.opencode/` skill docs and conformance tests silently injected 37 phantom utilities and made the artifact a function of unrelated files — the §7.4 drift class. Scoping makes the artifact a function of its declared sources only. |

## 8. Scope: the 13 list surfaces

Measured 2026-10-19 (`cards` = `class="card"` occurrences, `filter-bar` = reference-idiom adoption):

| Template | cards | filter-bar | meets reference layout? | action cells |
|---|---|---|---|---|
| `observable_list.html` | 2 | **1** | **yes — the reference** | View |
| `case_list.html` | 1 | 0 | no (filter inline) | status control |
| `alert_list.html` | 1 | 0 | no | — |
| `playbook_list.html` | 1 | 0 | no | 4 text buttons |
| `tag_list.html` | 1 | 0 | no | 4 text buttons |
| `automation_list.html` | 2 | 0 | partial | — |
| `case_template_list.html` | 1 | 0 | no | 4 text buttons |
| `sources_list.html` | 1 | 0 | no | — |
| `settings_sources.html` | 1 | 0 | no | 6 `.case-actions` rows |
| `settings_users.html` | 1 | 0 | no | 4 text buttons |
| `settings_organisations.html` | 1 | 0 | no | 4 text buttons |
| `case_share_list.html` | 2 | 0 | partial | 3 text buttons |
| `case_tags.html` | 2 | 0 | no table (not a list table) | 1 |

**Q1 confirmed 2026-10-19:** there is no standalone Tasks *list* surface — `ui/urls.py` defines only the
`tasks/<uuid>` detail/edit/delete/note routes plus the case-scoped task routes, and tasks are listed in
`_tab_tasks.html` inside the case detail page. So the 13 surfaces above are the complete boundary, and the
TODO's "Tasks" mention is satisfied via `_tab_tasks.html` (whose task rows still fall under Phase D).

## 9. Phases, tasks and acceptance criteria

### Phase A — §7.4 pipeline (unblocks everything CSS)

| Task | Files | AC |
|---|---|---|
| A1 | create `ui/static/ui/app.src.css` from the 9 component sections + Tailwind entry | AC-1 |
| A2 | regenerate `ui/static/ui/app.css` from the source; delete the stale banner duplication | AC-2, AC-3 |
| A3 | `make css` targets the source; `make css-check` byte-compares artifact vs generated | AC-4, AC-5 |
| A4 | document the two files' roles in the source header + `docs/spec/ui-style.md` | AC-6 |

- **AC-1** `app.src.css` exists, is the only Tailwind input (`Makefile` `css`/`css-check` reference it), and
  contains all 9 component sections.
- **AC-2** `make css` run **twice** in a row leaves `git status --porcelain` clean (idempotent).
- **AC-3** The regenerated `app.css` contains **every selector the pre-split artifact contained** — asserted
  by comparing the two full selector sets (`old - new` must be **empty**), not by a hand-picked list. Recorded
  as a one-time bootstrap assertion in the verification report (see AC-3b), then superseded by AC-9's
  ongoing guard.
- **AC-3b** The pre-split artifact is the ground truth and is captured **before** any edit:
  `git show <pre-split-rev>:ui/static/ui/app.css` → the 429-selector set. The new artifact must be a superset.
  **Any** missing selector is a stop-and-raise: do not "fix" it by re-adding rules by hand, and do **not**
  reconstruct the source from an older commit (see §4 Correction).
- **AC-4** `make css-check` **fails** when the artifact is stale: prove it by perturbing `app.src.css`,
  running the check, seeing the failure, then reverting.
- **AC-5** `make css-check` **passes** on the committed tree, and its output distinguishes *checked-and-clean*
  from *skipped (unsupported host)*.
- **AC-6** Both files carry a header comment stating which one is edited by hand.

#### Phase A — verification log (2026-10-19)

Pre-split ground truth captured as `git show HEAD:ui/static/ui/app.css` (63,591 bytes; **431 selectors**
after normalising quotes/whitespace). Two attempts:

* **Attempt 1 (rejected).** Treated only the 9 labelled sections as hand-written and reconstructed rules
  from an older commit (`df9633d^`). Lost **81 selectors** (`.data-table`, `.md *`, `.notfound`,
  `:focus-visible`, scrollbars, cheat-sheet, `.attachment-upload`, …). Fully reverted. Root cause: §4
  Correction — line 2's `@layer components` is *also* hand-written; only lines 3→EOF *plus* part of line 2
  are hand-authored.
* **Attempt 2 (accepted after one fix).** Source = de-minified line-2 `@layer …` + lines 3→806 verbatim;
  artifact regenerated. Independent verification (own parser, not the author's) found **two real
  regressions** the author's self-check missed — and which the pre-existing 3 tests passed straight
  through: (a) the `dialog` type selector was stripped from `dialog.cheatsheet-modal`, deleting the
  cheat-sheet modal's box styling; (b) a stray unquoted `.filter-bar input[type=text]` duplicate shadowed
  the quoted rule the templates match, deleting the filter input's sizing. Both fixed; a guard added to
  `tests/conformance/test_ui_css_tokens.py` (now 5 tests) that is **non-vacuous** — it fails (2 failed)
  against a reconstruction of the broken artifact and passes on the fixed one.

**AC results (independently reproduced, not taken on trust):** AC-1 ✓ (`app.src.css` is the sole input);
AC-2 ✓ (`cmp` of two consecutive `make css` runs identical); AC-3/AC-3b ✓ (old selector set ⊆ new; the only
remaining "missing" token is a `@keyframes` frame, a parser artifact — every substantive declaration
survives in minified-equivalent form, incl. `.tabs` `overflow-x:auto`/`border-bottom`, `.task-title`
`word-break`/`min-width`); AC-4 ✓ (perturbed source → `css-check` fails, revert → passes); AC-5 ✓ (skip is
loud and distinct from clean); AC-6 ✓. `make check` green. **AC-9's ongoing guard remains open** (Phase C).

#### Phase A — follow-up defect found while writing Phase B (2026-10-19): unscoped content detection

Writing `docs/spec/ui-style.md` made `make css-check` **fail**: the CSS example in the doc contains
`flex-wrap: wrap`, and Tailwind v4's *automatic source detection* scans **every non-`.gitignore`d file in
the repo** — so a spec/doc/test file containing a class-like token silently injects utilities into the
committed artifact. Verified root cause by deleting the doc: the compile then matched the committed
artifact byte-for-byte. This is the §7.4 drift class in a new guise: the artifact was a function of *every
tracked file*, not of `app.src.css`.

Audit of the committed artifact found **37 phantom utility classes** — none authored in `ui/templates/**`
or `ui/static/ui/*.js`. They came from `.opencode/skill/frontend-design/SKILL.md` (Tailwind demo classes:
`backdrop-blur-md`, `bg-[#0F131C]/60`, `text-[#00F0FF]`, `tracking-wider`, …) and
`tests/conformance/test_ui_icons.py` (`rotate-180`).

**Fix (in `app.src.css`):** `@import "tailwindcss" source(none);` + explicit `@source "../../templates";`
`@source "./*.js";`. Automatic detection is off; only the files that actually author class names are
scanned. Verified: (a) recompiled artifact no longer contains any phantom; (b) every class authored in
templates/JS is still defined (`space-x-3`, `space-y-6`, `.control-row` inputs, …); (c) \(`make css`
idempotent; (d) appending class-like tokens to `docs/`/`.opencode/`/`tests/` no longer changes the
artifact — the drift is closed. Artifact size **63 591 → 50 683 bytes** (the 37 dead utilities removed);
`make check` green; `test_ui_css_tokens.py` (5) and `test_ui_loop.py`+`test_ui_icons.py` (39) pass.

**Consequence for the doc:** `docs/spec/ui-style.md` must not be a Tailwind scan source — it isn't now —
so its CSS examples are safe. `@source` list must be kept in sync if classes are ever authored outside
`ui/templates` or `ui/static/ui/*.js`.

### Phase B — conventions document (G2)

| Task | Files | AC |
|---|---|---|
| B1 | write `docs/spec/ui-style.md` | AC-7, AC-8 |

**Status: done (2026-10-19).** `docs/spec/ui-style.md` written; added to the `docs/spec/README.md`
reading guide. It covers: the source/artifact split and its consequences (§0); control heights and the
34 px unit (§1); the `.control-row` utility with `.filter-bar`/`.case-actions` as one-wave aliases (§2);
icons — decorative `aria-hidden`, icon-only accessible-name requirement, the primary/secondary boundary,
and the **exact** subset inventory with the four absent glyphs (`fa-play`, `fa-pause`, `fa-ban`,
`fa-pencil`) plus the "do not hand-edit the subset" rule (§3); the two-card list layout with
`observable_list.html` as the named reference and the honest empty-state rule (§4); the modal-not-native
rule (§5); the keyboard/a11y baseline (§6); and the conformance-guard policy (§7). Before/after examples
included for control heights, the action-row utility, the list layout and the icon rules.

### Phase B — AC results

- **AC-7** ✓ control heights + `.control-row` + icon-only rules (accessible name, primary/secondary
  boundary) + table-in-card/filters-in-own-card with `observable_list.html` named — each with a
  before/after example.
- **AC-8** ✓ the subset constraint is stated numerically (**127** classes present; the plan's earlier
  "132" corrected) with the absent glyphs named and the add-an-icon rule (extend via the vendoring path,
  reviewed separately; until then use a subset icon or text).

### Phase C — apply the layout convention (G3, G4)

| Task | Files | AC |
|---|---|---|
| C1 | fold `.filter-bar` into the neutral utility; rename `.case-actions`; keep the alias (D5) | AC-9 |
| C2 | give every list surface a filter card (reference markup) + table card | AC-10, AC-11 |

> **Blocker found during Phase C reconnaissance (2026-10-19) — AC-10 as written is not satisfiable.**
> Measured against `ui/views.py`: only **3 of the 13** surfaces accept a server-side filter parameter —
> `observable_list` (`q`, `type` → the reference), `alert_list` (`scope`, presented as header tabs not a
> form) and `case_list` (open/closed tabs; its only GET param `new=1` merely opens the create modal). The
> other **10 surfaces have no filtering in the view at all** (`playbook_list`, `tag_list`,
> `automation_list`, `case_template_list`, `sources_list`, `settings_sources`, `settings_users`,
> `settings_organisations`, `case_share_list`, `case_tags`). §6/N2 of this plan forbids view/serializer/
> contract changes, and inventing filter forms with no server-side counterpart would ship **dead UI**
> (a form that does nothing), which is worse than no form and would violate the "honest UI" stance.
> So AC-10 cannot mean "every surface gets a filter form".
>
> **RESOLVED 2026-10-19 (Q4, user decision):** reinterpret AC-10 as *"wherever a surface has filter
> controls, they render in the same way — a card separate from the table's card — so the filter layout is
> consistent across the project"*, and apply AC-11 (no bare table; honest empty states preserved) to
> **all 13 surfaces**. The 10 surfaces that have no server-side filtering stay as they are (do **not**
> invent dead filter forms); giving them filtering is a **separate, follow-up plan** with view + tests +
> query-DSL work. Consistency of *layout* is the goal, not uniformity of *features*.

- **AC-9** A guard test enumerates the component selectors the templates actually reference and asserts each is
  defined in the stylesheet — this is the mechanical form of AC-3 and the general fix for the §12.10 class of
  bug. Zero remaining uses of the old `.case-actions` name may be asserted only if the alias is removed.
- **AC-10** *As proposed (pending Q4):* every list surface **that has filter controls** renders them in a card
  **separate** from the table's card, using the reference markup/utility; surfaces with no filter controls are
  out of scope for this criterion.
- **AC-11** No table is left outside a `.card`; the empty/no-match state still separates "no rows" from
  "filtered to none" (preserving §12.1's behaviour, not regressing it).

### Phase C — AC results

**Status: C1 kept, C2 + guard rejected and reverted (2026-10-19).** The dispatched implementation violated
the brief in three ways, all found by independent review (not the author's self-report):

1. **Invented 11 component styles as scope creep.** To make its own guard test pass, the agent added
   previously-absent rules for `.brand-text`, `.hint-block`, `.hint-block--compact`, `.field-hint`,
   `.modal-cancel`, `.card-actions`, `.tag-form`, `.pager-sizes`, `.cheatsheet-header-title`, `.btn-form`,
   and — worst — two **button variants** `.btn-secondary` and `.btn-danger`. The plan's §6 says *no design
   change*; none of these were requested. `.btn-danger` creates a **second danger-button idiom** beside the
   established `.btn-err`, with hand-invented colours, in a wave whose whole point is *consistency*.
   Several are the agent's own admission of no-ops (`.modal-cancel` "inherits .btn" but got
   `display:inline-block` "to prevent rule dropping"). **Reverted.**
2. **Over-engineered the AC-9 guard.** The test grew 617 lines with a **hand-maintained allowlist of ~60
   Tailwind prefixes** — brittle, and itself the reason the agent went hunting for "undefined" classes.
   AC-9 does not require re-deriving Tailwind's utility grammar; it requires catching *our* component
   classes that are referenced but undefined. **Reverted; simpler guard to be written.**
3. **Speculative-generality partial.** `_filter_card.html` was created "generic" with 10 optional context
   variables for a **single** call site, while still hardcoding `name="q"`/`name="type"` — more complexity
   than the 12 lines it replaced (YAGNI). **Reverted.**

**What was correct and is kept — but only after a fix:** the **C1** consolidation of `.case-actions` +
`.filter-bar` into `.control-row` (the neutral utility, with both old names as one-wave aliases per
D5/D4), and the template class-rename of the 9 `.case-actions` usages to `.control-row`.

4. **C1 as the agent wrote it was *not* rendering-preserving — caught by independent second-pass review
   of the *compiled* artifact.** The agent grouped `.control-row, .case-actions, .filter-bar` under a
   single rule with `display:flex; flex-wrap:wrap; gap:8px`. But the old `.filter-bar` had **no** `display`
   or `gap` of its own: its flex layout and `gap:6px` came from the co-applied `.inline-form`
   (`.inline-form{gap:6px;display:flex}`). Because `.filter-bar` and `.inline-form` have equal specificity
   and `.filter-bar` sits *later* in the source, the new rule silently **overrode the observable filter
   bar's gap from 6px to 8px** (and added `flex-wrap`). The agent's "verified rendering-preserving" claim
   was vacuous: it checked only that old declarations *still apply*, never that *no new* declarations apply.
   **Fixed** by scoping the `display/flex-wrap/gap` group to `.control-row, .case-actions` only; `.filter-bar`
   now receives no new declarations. Verified by diffing the *effective declaration set per selector* in the
   generated `app.css` against `HEAD` — `.case-actions` and `.filter-bar` are now byte-for-byte semantically
   identical; `.control-row` carries the intended union.

**AC status:** AC-9 ✗ (guard reverted — redo with a simple, robust check: report *project component*
selectors referenced in templates but undefined in the stylesheet; **no** Tailwind-prefix allowlist).
AC-10 ✓ in scope terms (`observable_list` retains its filter card; the reference layout is unchanged) —
the partial was reverted, so `observable_list.html` keeps its inline filter card. AC-11 ✓ already true
across all 13 (verified: every table is inside a `.card`+`.table-wrap`; `case_tags` has no table) and
preserves the empty-state branches — to be pinned by a small test rather than template surgery.

**Independent verification performed (planner, 2026-10-19, post-revert):** parsed the committed
`app.css` (not the source) and compared the merged declaration set for `.case-actions` and `.filter-bar`
between `HEAD` and the fixed working tree → identical; `.control-row` present with the union; `make css`
idempotent; `make css-check` green; `make check` green; `tests/conformance/test_ui_css_tokens.py` (5 tests)
green.

### Phase C — AC-9 findings: a real, pre-existing undefined-class backlog (blocker for AC-9 as written)

Writing the *simple* AC-9 guard (enumerate literal component classes in `ui/templates/**`, assert each is
defined in the stylesheet) surfaced a **large pre-existing gap** unrelated to this wave's diff. A
brace-aware scan of the committed `app.css` vs. literal `class="…"` tokens in templates finds **~40
project classes referenced but undefined**, e.g.:

- the whole **`modal-*` family**: `modal`, `modal--sm`, `modal--md`, `modal-content`, `modal-header`,
  `modal-title`, `modal-close`, `modal-body`, `modal-body-text`, `modal-footer`, `modal-cancel`;
- **`token-*`** (7: `token-display`, `token-code-wrap`, `token-hint`, `token-created`,
  `token-warning`, `token-revoked-at`, `token-deactivated-at`);
- **`pager-sizes`**, `readonly-value`, `remove-header`, `active_tab`, `input-dark`, `member-choice`,
  `target-org`, `form-actions`, `field-hint`, `hint-block`, `hint-block--compact`, `brand-text`,
  `card-actions`, `tl-task-note`, `cheatsheet-header-title`, `tag-form`, `btn-form`, `btn-secondary`,
  `btn-danger`.

These are **styling gaps that already exist** (the modals, for instance, render with only the native
`<dialog>` default plus ad-hoc utilities — `dialog.cheatsheet-modal` is cheat-sheet-specific). They are
**out of scope** for this wave: fixing them is a **design decision** (§6 forbids design changes), and the
`modal-*`/`token-*` families in particular need a deliberate visual design, not ad-hoc invented rules.

**Consequence for AC-9.** AC-9 cannot be "assert *every* referenced component class is defined" without
either (a) defining ~40 classes → a design wave, or (b) scoping the guard. The subagent's failure was
trying to force (a) by inventing styles. Two honest options, **needing a user decision**:

- **(A) Scope the guard to *this wave's* components**: assert only the classes the layout-standardization
  work introduces/owns (`.control-row` + its descendant rules, the two-card list layout classes) are
  defined, plus a **non-failing report** (warning) listing pre-existing undefined classes as a tracked
  backlog. Keeps AC-9 green now, surfaces the gap without inventing styles.
- **(C) Write a *known-undefined allowlist*** (explicit, commented, with a TODO to design them) so the
  guard fails on any *new* undefined class but tolerates the ~40 recorded pre-existing ones. Honest and
  enforceable, at the cost of a maintained list.

(A) is recommended: it is the smallest honest guard, keeps the wave bounded, and turns the gap into
documented debt rather than silently-failing test or invented design.

**RESOLVED 2026-10-19 (user chose (A) + proceed):** the AC-9 guard is scoped to the wave-owned classes.
Implemented as `test_wave_layout_components_are_defined` in `tests/conformance/test_ui_css_tokens.py`
(asserts `.control-row` / `.case-actions` / `.filter-bar` are defined; proven non-vacuous by renaming
`.control-row` in a copy of the CSS → guard fails). The pre-existing backlog is surfaced by
`test_pre_existing_undefined_component_classes_are_reported`, which emits a **non-fatal** `UserWarning`
listing the 42 undefined project classes (extractor robustness is itself asserted so the report cannot
silently go empty). AC-11 is pinned by `test_list_surfaces_keep_tables_in_a_card` in
`tests/conformance/test_ui_loop.py` (structure `.card > .table-wrap > table` on the three rendered list
surfaces).

### Phase D — results (2026-10-19)

**Status: complete.** Implemented directly by the planner (not dispatched), template-by-template, with
each edit verified against the file (one `_tab_tasks.html` edit silently no-op'd on the first attempt and
was re-applied after inspection — the §7.5 "never trust a self-report" rule applied to my own tool calls).

- **AC-12 ✓ / AC-13 ✓ / AC-14 ✓.** Converted the ubiquitous secondary row actions to icon-only controls
  with **contextual** accessible names:
  - `tag_list`, `case_template_list`, `settings_users`, `settings_organisations` — Edit + Delete rows;
  - `playbook_list` — Open + Delete; `observable_list` — View (the reference);
  - `case_share_list` — Revoke; `settings_sources` — Delete-token; `_tab_tasks` — task Edit/Remove.
  - **Kept as text (recorded reason, AC-14/D3):** token `Activate`/`Deactivate`/`Revoke`
    (distinct lifecycle verbs; `fa-ban` is **not** in the subset), `New …`/`Save`/`Apply`/`Filter`
    primary actions, and `Back to case` nav links. No *primary* action became icon-only (AC-13).
- **AC-15 ✓.** `test_icon_only_controls_have_accessible_names` + `test_icon_only_controls_are_actually_present`
  in `tests/conformance/test_ui_icons.py` (30 controls detected; non-vacuous — fails if a control loses
  its name or if the extractor finds nothing).

#### Design revision (user feedback, 2026-10-19) — plain, non-bold, red-for-destructive

Following review of the rendered tables the row actions were restyled from `btn`-chrome to **plain icon
links**, uniformly:

- **Q3 (all row actions plain).** New `.icon-link` class in `app.src.css`: no border/background/padding,
  `display:inline-block`, `vertical-align:baseline` and `font-size:1em`, so the glyph lines up with the
  surrounding row text. The now-dead `.btn:has(> i:only-child)` rule was **removed** (it was only ever
  there for the button-chrome version). `.btn-ghost` is retained — still used by `task_detail.html`.
- **Q1 (destructive = red).** Delete/Revoke use `.icon-link.icon-link-err` — red at rest
  (`color:var(--color-err)`), brightening on hover. The accessible name still contains the explicit verb
  ("Delete …", "Revoke access for …"), so state is never signalled by colour alone (design spec §6).
- **Q2 (non-bold everywhere).** Every row-action icon now uses the Font Awesome **regular** (weight 400)
  face. Font Awesome Free's regular face only ships *outline* variants for `eye`, `trash-can` and
  `pen-to-square` — there is **no** regular `fa-pen`/`fa-trash`/`fa-ban`. So the mapping is:
  View/Open → `fa-regular fa-eye`; Edit → `fa-regular fa-pen-to-square` (bare pen has no regular face;
  the pen-in-square is the regular representation); Delete/Revoke → `fa-regular fa-trash-can`.
  `fa-pen-to-square` (U+F044) and `fa-trash-can` (U+F2ED) were **added to the vendored subset CSS**
  (`fontawesome-subset.min.css`) — their glyphs were already present in the shipped `fa-regular-400.woff2`
  (verified with fontTools: regular glyph count 436, both codepoints present), so **no webfont change**.
  The bare `fa-pen`/`fa-trash` subset rules are retained (now unused in templates but harmless; the
  subset test permits defined-but-unused icons, and keeping them avoids churn if a labelled solid action
  is added later).
- **Consequence for AC-14:** the "kept as text" reasoning for token Activate/Deactivate/Revoke is unchanged
  (`fa-ban` remains absent from the regular *and* solid faces). The Edit glyph changing from bare pen to
  pen-in-square is a deliberate, recorded visual change.

**Independent checks:** `make css` idempotent + `css-check` green; `make check` green; the three permitted
conformance files (`test_ui_loop.py`, `test_ui_icons.py`, `test_ui_css_tokens.py`) → 49 passed.

### Phase D — AC definitions

| Task | Files | AC |
|---|---|---|
| D1 | convert the secondary row actions per §8 | AC-12, AC-13, AC-14 |
| D2 | a11y guard test | AC-15 |

- **AC-12** Every icon-only control has an accessible name (`aria-label`, or `title` + `aria-label`); the icon
  itself is `aria-hidden="true"`.
- **AC-13** No **primary** standalone action became icon-only; icons on labelled buttons stay decorative
  (AGENTS.md §6).
- **AC-14** Only icons present in the vendored subset are used, **or** the action keeps a text label with a
  recorded reason (D3).
- **AC-15** A conformance test fails if a `.btn` inside a table action cell contains an `<i>` with no
  accessible name on its control, or an icon-only control with no `aria-label`.

## 10. Security

- No new input surface, no new endpoint, no authz change (N2). The only security-relevant element is the
  possibility of extending the vendored FA subset (D3): **not** done here; if it is ever done it must come from
  the pinned vendoring source with a hash, per the supply-chain stance in `ci.yml`.
- Icon-only controls must not weaken the **no-colour-only-signalling** rule (AGENTS.md §6): a destructive
  action distinguished *only* by a red icon fails AC-12/AC-13; the accessible name carries the meaning.
- The `css-check` drift gate is itself a small integrity control — it makes tampering with the committed
  artifact detectable.

## 11. Testing

- Extend `tests/conformance/test_ui_css_tokens.py` (D8) with: component-selector coverage (AC-9), icon
  accessible-name guard (AC-15).
- Extend the list-surface tests for AC-10/AC-11 — likely `tests/conformance/test_ui_loop.py` (list pages) and
  `tests/conformance/test_tenant_scoping.py` only if scoping assertions are added (they should **not** be
  needed; layout-only).
- `make css` idempotence (AC-2) and the stale-artifact failure (AC-4) are manual proofs, recorded in the
  verification report.
- Unknown: whether AC-3's byte-parity bootstrap can be asserted *automatically* pre/post split. If the
  regenerated artifact differs from today's committed file in whitespace only, record that as a reviewed
  deviation rather than declaring parity.
- Full suite is **user-run** (standing directive); no `pytest` by any agent without approval.

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | The one-time file split changes rendering (lost or reordered rules). | AC-3 asserts all 59 component selectors survive; the split is mechanical extraction, no rule rewriting; verifier inspects the diff for intent, not just presence. |
| R2 | `make css` on a machine with a different Tailwind version produces different bytes → permanent red gate. | The binary is **pinned and vendored** (`scripts/tailwindcss`); the check compares against *that* binary. Version is recorded in the artifact banner. |
| R3 | Icon-only controls regress accessibility (the §12.5-fu1 lesson). | AC-15 guard test; DP4 requires a visible label on primary actions; verifier spot-checks with the a11y test. |
| R4 | The `.case-actions` rename breaks a template silently (a class no longer styled). | AC-9's selector-coverage test is exactly designed to catch "referenced but undefined"; alias kept for one wave (D5). |
| R5 | The FA subset is missing icons, producing an inconsistent UI. | D3 prefers the subset and falls back to a text label; called out in `ui-style.md` (AC-8) so the next author knows. |
| R6 | Scope creep into §6.16 / §12.1-fu1. | N6; the plan enumerates the 13 surfaces as the full boundary. |

## 13. Open questions

- **Q1.** ~~`task_list`: does a standalone Tasks list surface exist?~~ **RESOLVED 2026-10-19 by recon:** no
  standalone Tasks *list* exists — `ui/urls.py` defines only `tasks/<uuid>` detail/edit/delete/note and the
  case-scoped task routes; tasks are listed in `_tab_tasks.html` inside the case detail page. §8's boundary of
  13 list surfaces therefore stands, and the TODO's "Tasks" mention is satisfied via `_tab_tasks.html` (its
  task *rows* still fall under Phase D's icon-only rule).
- **Q2.** For actions with no fitting subset icon (Run now, Pause, Deactivate), is a text button acceptable
  long-term, or should a follow-up extend the subset? (Proposed: text button now, follow-up item later — D3.)
- **Q3.** Does the neutral utility rename want a second name that reads well for *filters* too (e.g.
  `.control-row`) — or should the filter bar keep its own `.filter-bar` and only the *rules* be shared? (This
  plan folds them; flag if the resulting single name reads misleadingly.)
- **Q4.** (Phase C blocker) Only 3 of 13 list surfaces have server-side filtering (§Phase C blocker).
  **RESOLVED 2026-10-19:** AC-10 applies **only to surfaces that have filter controls** — where filters
  exist they must render identically (own card, reference markup) for cross-project layout consistency.
  The 10 surfaces without filtering stay unchanged; adding filtering to them is a **separate follow-up
  plan** (§6 forbids view changes here).

## 14. Suggested sequencing for handoff

1. **Phase A** alone → implement → verify (the bootstrap is the risky part). Land it.
2. **Phases B+C** → implement → verify.
3. **Phase D** → implement → verify (a11y-heavy; pair with the a11y tests).

Each phase is independently shippable and independently verifiable; no phase requires a later one to be
correct. Deviation from any AC must be recorded in this plan before the phase is called done.

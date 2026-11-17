#!/usr/bin/env bash
#
# scripts/secret-scan.sh — deterministic secret scanner for the Amalthea repository.
#
# The repository is PUBLIC: anything that reaches git history is world-readable and,
# in practice, permanent. This script is the enforcement half of the "never commit
# secrets" policy in SECURITY.md, on four surfaces:
#
#   --staged    the blobs staged for commit (index, via `git grep --cached`)  <- pre-commit
#   --tracked   the committed tree at HEAD (objects, via `git grep HEAD`)     <- pre-push
#   --history   every blob, commit message and annotated-tag message of every
#               ref (`git rev-list --all` + `git grep` + `git log`/`for-each-ref`)  <- CI
#   --files F…  explicit paths read from the working tree                     <- tests/manual
#
# Scanning git OBJECTS rather than the working tree is the point: a secret that was
# staged and then edited out of the working copy is still in the index, and a secret
# committed and later deleted is still in history. --files is the only mode that
# reads the working tree, and only because it is asked to.
#
# Design rules:
#   * dependency-free: git + bash, preferring ripgrep and falling back to grep
#   * deterministic: a fixed pattern table below — no entropy heuristics, no network
#   * fail closed: a scanner/tool error is exit 2; truncated or unparseable scanner
#     output is reported as a finding (exit 1) — neither can pass silently
#   * safe: scanned content is only ever read as data (never eval'd or executed),
#     records are NUL-framed so spaces and colons in paths survive, and findings
#     print `file:line: <label>` — the matched secret itself is never echoed
#
# Exit codes: 0 = clean (one line), 1 = findings (report + remediation block),
#             2 = usage error or scanner failure.

set -euo pipefail

BATCH_SIZE=64   # files handed to the search tool per invocation (ARG_MAX headroom)
BLOB_BATCH=512  # unique blob ids handed to `git grep` per invocation

usage() {
    cat <<'EOF'
Usage: scripts/secret-scan.sh [--staged | --tracked | --history | --files FILE...]

  --staged          content staged for commit (default; scans the git index, not the worktree)
  --tracked         the committed tree at HEAD (scans git objects, not the worktree)
  --history         every blob + commit/tag message of every ref (CI backstop)
  --files FILE...   explicit paths read from the working tree (the only worktree mode)

Exit: 0 clean · 1 findings (blocks the commit/push) · 2 usage/scanner error.
Findings and the remediation block go to stdout; diagnostics go to stderr.
EOF
}

# ---------------------------------------------------------------------------
# Pattern table: "<scope><TAB><label><TAB><extended regex>".
#   token — a credential-shaped token: matched EVERYWHERE, including on
#           allowlisted paths (the allowlist can never hide a real token).
#   url   — a credential-bearing URL: matched everywhere except allowlisted
#           paths, whose reason for being allowlisted is a placeholder
#           user:pass@host literal in a doc or fixture.
# High-specificity patterns only. Deliberately NO generic `password=` / `Bearer`
# rules: they fire on documentation and on intentional test fixtures, and a
# scanner that cries wolf gets disabled or bypassed with --no-verify.
# Every pattern must compile in all three engines it is fed to — ripgrep
# (Rust regex), grep -E and `git log --grep` (POSIX ERE) — which
# tests/unit/test_secret_scan.py asserts.
# ---------------------------------------------------------------------------
emit_patterns() {
    printf '%s\n' \
        $'token\tGitHub personal access token (fine-grained)\tgithub_pat_[A-Za-z0-9_]{20,}' \
        $'token\tGitHub token (classic)\tghp_[A-Za-z0-9]{36}' \
        $'token\tGitHub token (OAuth / user-to-server / server / refresh)\tgh[ousr]_[A-Za-z0-9]{36}' \
        $'token\tAWS access key ID\tAKIA[0-9A-Z]{16}' \
        $'token\tSlack token\txox[baprs]-[A-Za-z0-9-]{10,}' \
        $'token\tGoogle API key\tAIza[0-9A-Za-z_-]{35}' \
        $'token\tStripe live secret key\tsk_live_[0-9A-Za-z]{24,}' \
        $'token\tStripe live restricted key\trk_live_[0-9A-Za-z]{24,}' \
        $'token\tPEM private key block\t-----BEGIN [A-Z ]*PRIVATE KEY-----' \
        $'url\tCredential-bearing URL (user / password inside a URL)\t[a-z][a-z0-9+.-]*://[^/@[:space:]:]+:[^/@[:space:]]+@'
}

# ---------------------------------------------------------------------------
# Allowlist — exact repo-relative paths where the `url` (credential-URL)
# pattern is a KNOWN placeholder, not a credential. `token` patterns are still
# reported on these paths; content here is scanned like anywhere else, only the
# url-scoped finding is suppressed (see record_hits). Entries need a reason.
# Do NOT add directories, globs, or "tests/" — the list must stay short enough
# for a reviewer to check in one glance. Do NOT add docs/planning/* to silence
# a finding: the scanner output is a bug report against the document; reword
# the document instead.
# ---------------------------------------------------------------------------
allowlisted() {
    case "$1" in
        .env.example)
            # Committed template. Documents a local Postgres URL whose user/password
            # are the dev placeholders; the file even says "NEVER commit a real .env".
            return 0 ;;
        tests/conformance/test_automation_executor.py)
            # Asserts that outbound automation requests refuse URLs that carry
            # userinfo; uses a dummy "user:pass@host" fixture literal.
            return 0 ;;
        tests/conformance/test_errors.py)
            # Asserts that connection errors are raised (not logged with secrets);
            # uses a dummy analyst-style credential-in-URL literal.
            return 0 ;;
        *)
            return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# Filename guard: these names must never be tracked, whatever the content.
# ---------------------------------------------------------------------------
filename_violation() {
    local base="${1##*/}"
    case "$base" in
        .env.example)
            # The committed template is the one dotenv name we allow.
            return 1 ;;
        .env|.env.*|*.env|*.env.*|*.envrc)
            printf "environment file '%s' must stay untracked (see .gitignore)" "$base"
            return 0 ;;
        *.pem|*.key)
            printf "key material file '%s' (*.pem / *.key) must stay untracked" "$base"
            return 0 ;;
        id_rsa|id_ed25519)
            printf "SSH private key '%s' must stay untracked" "$base"
            return 0 ;;
        *)
            return 1 ;;
    esac
}

# Never scan (or report) anything inside these — dependency and cache artifacts.
# Applies to --files and to --history's legacy blobs; --staged/--tracked still
# scan every current tracked file, because that is exactly what would publish.
excluded_path() {
    case "$1" in
        .git|.git/*|.venv|.venv/*|node_modules|node_modules/*) return 0 ;;
        */.git/*|*/.venv/*|*/node_modules/*|*/__pycache__/*)   return 0 ;;
        *.pyc|*.pyo)                                            return 0 ;;
        *) return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# Search tool: ripgrep when present, grep -REn as the portable fallback.
# ---------------------------------------------------------------------------
if command -v rg >/dev/null 2>&1; then
    SEARCH_TOOL="rg"
else
    SEARCH_TOOL="grep"
fi

# content_hits PATTERN FILE... — NUL-framed records on stdout: "path\0line:match\n".
# Return: 0 = hits, 1 = none, >=2 = tool failure (caller must abort).
content_hits() {
    local pat="$1" rc=0
    shift
    if [ "$SEARCH_TOOL" = "rg" ]; then
        # -a/--text: a token hidden behind a NUL byte must still be found — the
        # default "binary file matches" notice carries no path:line record and
        # would drop the finding. -H: keep the path even in a one-file batch
        # (the framing depends on it). Do NOT add --binary: it re-enables the
        # notice even alongside -a.
        rg --null -n -H --no-heading --color never -o -a -e "$pat" -- "$@" || rc=$?
    else
        grep --null -HREnoa -e "$pat" -- "$@" || rc=$?
    fi
    return "$rc"
}

workdir="$(mktemp -d)"
findings_file="$workdir/findings"
trap 'rm -rf "$workdir"' EXIT

# record_hits SCOPE LABEL FRAMING — stdin: NUL-framed scanner records.
#   text  "path\0line:match\n"       ripgrep / grep over working-tree files
#   index "path\0line\0match\n"       git grep --cached (path kept verbatim)
#   rev   "rev:path\0line\0match\n"   git grep <rev> (a rev never contains ':',
#                                     so the FIRST colon is the separator — this
#                                     is what keeps a path like "a:b.txt" intact)
# Never prints the match itself. Anything that does not parse as a record is
# reported as a finding anyway (fail closed) with the content withheld: silently
# skipping it is how a scanner fails open on a tool-output change.
record_hits() {
    local scope="$1" label="$2" framing="$3"
    local path field lineno rc
    while true; do
        path=""; rc=0
        IFS= read -r -d '' path || rc=$?
        if [ "$rc" -ne 0 ]; then
            if [ -n "$path" ]; then
                # Partial field at EOF: data without a terminator. Could be the
                # secret itself — report, but never echo it.
                printf '%s:0: %s (truncated scanner record — content withheld)\n' \
                    '?' "$label" >>"$findings_file"
            fi
            break
        fi
        if [ -z "$path" ]; then
            printf '%s:0: %s (empty path in scanner record — content withheld)\n' \
                '?' "$label" >>"$findings_file"
            continue
        fi
        if [ "$framing" = "rev" ]; then
            case "$path" in
                *:*) path="${path#*:}" ;;
                *)
                    printf '%s:0: %s (unparseable scanner record — content withheld)\n' \
                        '?' "$label" >>"$findings_file"
                    continue ;;
            esac
        fi
        lineno=""
        if [ "$framing" = "text" ]; then
            field=""; rc=0
            IFS= read -r field || rc=$?
            if [ "$rc" -ne 0 ] && [ -z "$field" ]; then
                printf '%s:0: %s (truncated scanner record — content withheld)\n' \
                    "$path" "$label" >>"$findings_file"
                break
            fi
            lineno="${field%%:*}"
        else
            field=""; rc=0
            IFS= read -r -d '' field || rc=$?
            if [ "$rc" -ne 0 ] && [ -z "$field" ]; then
                printf '%s:0: %s (truncated scanner record — content withheld)\n' \
                    "$path" "$label" >>"$findings_file"
                break
            fi
            lineno="$field"
            field=""; rc=0
            IFS= read -r field || rc=$?
            if [ "$rc" -ne 0 ] && [ -z "$field" ]; then
                printf '%s:%s: %s (truncated scanner record — content withheld)\n' \
                    "$path" "$lineno" "$label" >>"$findings_file"
                break
            fi
        fi
        case "$lineno" in
            ''|*[!0-9]*)
                printf '%s:0: %s (unparseable scanner record — content withheld)\n' \
                    "$path" "$label" >>"$findings_file"
                continue ;;
        esac
        path="${path#./}"
        if [ "$MODE" = "--files" ] && excluded_path "$path"; then
            continue
        fi
        if [ "$scope" = "url" ] && allowlisted "$path"; then
            continue
        fi
        printf '%s:%s: %s\n' "$path" "$lineno" "$label" >>"$findings_file"
    done
    return 0
}

# guard_filenames MODE — stdin: NUL-separated paths.
#   object   the path comes from the git index or a tree: no working-tree
#            check, because the object is exactly what would be published.
#            History mode also skips dependency/cache artifacts (below).
#   worktree paths are user-supplied: skip ones that do not exist right now
guard_filenames() {
    local require="$1" f reason
    while IFS= read -r -d '' f; do
        if [ -z "$f" ]; then
            continue
        fi
        f="${f#./}"
        if { [ "$MODE" = "--files" ] || [ "$MODE" = "--history" ]; } && excluded_path "$f"; then
            continue
        fi
        if [ "$require" = "worktree" ] && [ ! -e "$f" ]; then
            continue
        fi
        if reason="$(filename_violation "$f")"; then
            printf '%s:0: forbidden file name — %s\n' "$f" "$reason" >>"$findings_file"
        fi
    done
    return 0
}

# ---------------------------------------------------------------------------
# Argument parsing (first argument selects the mode; default --staged).
# ---------------------------------------------------------------------------
MODE="${1:-}"
if [ -z "$MODE" ]; then
    MODE="--staged"
else
    case "$MODE" in
        -h|--help)
            usage
            exit 0 ;;
        --staged|--tracked|--history|--files)
            shift ;;
        *)
            printf 'secret-scan: unknown mode %s\n\n' "$MODE" >&2
            usage >&2
            exit 2 ;;
    esac
fi

if [ "$MODE" = "--files" ]; then
    if [ "$#" -eq 0 ]; then
        echo "secret-scan: --files needs at least one path" >&2
        usage >&2
        exit 2
    fi
elif [ "$#" -gt 0 ]; then
    printf 'secret-scan: %s takes no path arguments (got: %s)\n\n' "$MODE" "$*" >&2
    usage >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Collect the paths / commits to examine (git-object modes run from the repo
# root; --files stays in the caller's directory).
# ---------------------------------------------------------------------------
staged_paths=()
tree_paths=()
files=()
commits=()
has_head=0
scan_files=()
history_blobs=()
history_blob_paths=""
candidate_blobs=""

case "$MODE" in
    --staged|--tracked|--history)
        if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
            echo "secret-scan: not inside a git work tree (required for $MODE)" >&2
            exit 2
        fi
        cd "$repo_root"
        ;;
esac

case "$MODE" in
    --staged)
        # ACMR: a staged deletion is a fix, not a violation (do not flag it).
        while IFS= read -r -d '' f; do
            if [ -n "$f" ]; then staged_paths+=("$f"); fi
        done < <(git diff --cached --name-only --diff-filter=ACMR -z)
        ;;
    --tracked)
        if git rev-parse -q --verify HEAD >/dev/null 2>&1; then
            has_head=1
            while IFS= read -r -d '' f; do
                if [ -n "$f" ]; then tree_paths+=("$f"); fi
            done < <(git ls-tree -r --name-only -z HEAD)
        fi
        ;;
    --files)
        for f in "$@"; do
            f="${f#./}"
            files+=("$f")
            if excluded_path "$f"; then
                continue
            fi
            # Regular files only: a directory argument could hide an ignored or
            # renamed secret, and rg/grep differ on how they walk it. Callers
            # pass files; anything else is reported in the scope line, not scanned.
            if [ -f "$f" ]; then
                scan_files+=("$f")
            fi
        done
        ;;
    --history)
        while IFS= read -r rev; do
            if [ -n "$rev" ]; then commits+=("$rev"); fi
        done < <(git rev-list --all)
        ;;
esac

# ---------------------------------------------------------------------------
# 1) Filename guard.
# ---------------------------------------------------------------------------
case "$MODE" in
    --staged)
        if [ "${#staged_paths[@]}" -gt 0 ]; then
            printf '%s\0' "${staged_paths[@]}" | guard_filenames object
        fi
        ;;
    --tracked)
        if [ "$has_head" -eq 1 ] && [ "${#tree_paths[@]}" -gt 0 ]; then
            printf '%s\0' "${tree_paths[@]}" | guard_filenames object
        fi
        ;;
    --files)
        if [ "${#files[@]}" -gt 0 ]; then
            printf '%s\0' "${files[@]}" | guard_filenames worktree
        fi
        ;;
    --history)
        # One diff walk instead of one `git ls-tree` per commit: every path that
        # ever appeared is still checked, and NUL framing survives odd filenames.
        if [ "${#commits[@]}" -gt 0 ]; then
            if ! git log --all --pretty=format: --name-only -z | guard_filenames object; then
                echo "secret-scan: git log failed while listing history paths" >&2
                exit 2
            fi
        fi
        ;;
esac

# ---------------------------------------------------------------------------
# 2) Content scan — one pass per pattern, always over git objects except --files.
# ---------------------------------------------------------------------------

# scan_index: `git grep --cached` on the staged paths (index blobs, not the
# working tree). :(literal) pathspecs so glob or colon characters in a file
# name cannot change what matches.
scan_index() {
    local scope="$1" label="$2" pattern="$3"
    local i=0 rc p
    local -a specs
    while [ "$i" -lt "${#staged_paths[@]}" ]; do
        specs=()
        for p in "${staged_paths[@]:i:BATCH_SIZE}"; do
            specs+=(":(literal)$p")
        done
        rc=0
        git grep -E -a -n -o -z -e "$pattern" --cached -- "${specs[@]}" \
            | record_hits "$scope" "$label" index || rc=$?
        if [ "$rc" -ge 2 ]; then
            echo "secret-scan: git grep --cached failed (exit $rc) while scanning for: $label" >&2
            exit 2
        fi
        i=$((i + BATCH_SIZE))
    done
}

# scan_revs: `git grep <rev>…` over whole trees (no pathspec). Revs first —
# after `--` git would treat them as paths.
scan_revs() {
    local scope="$1" label="$2" pattern="$3"
    shift 3
    local rc=0
    git grep -E -a -n -o -z -e "$pattern" "$@" \
        | record_hits "$scope" "$label" rev || rc=$?
    if [ "$rc" -ge 2 ]; then
        echo "secret-scan: git grep failed (exit $rc) while scanning for: $label" >&2
        exit 2
    fi
}

# scan_worktree_files: the one mode that reads the working tree.
scan_worktree_files() {
    local scope="$1" label="$2" pattern="$3"
    local i=0 rc
    local -a batch
    while [ "$i" -lt "${#scan_files[@]}" ]; do
        batch=("${scan_files[@]:i:BATCH_SIZE}")
        rc=0
        content_hits "$pattern" "${batch[@]}" \
            | record_hits "$scope" "$label" text || rc=$?
        if [ "$rc" -ge 2 ]; then
            echo "secret-scan: search tool failed (exit $rc) while scanning for: $label" >&2
            exit 2
        fi
        i=$((i + BATCH_SIZE))
    done
}

# emit_candidate_prefixes: cheap fixed strings that must cover every pattern in
# emit_patterns. The history scan first collects blobs containing any prefix,
# then runs the exact regexes only on those candidates. This keeps a full-history
# scan bounded; scanning every commit tree for every pattern did not.
emit_candidate_prefixes() {
    printf '%s\n' \
        'github_pat_' \
        'ghp_' 'gho_' 'ghu_' 'ghs_' 'ghr_' \
        'AKIA' \
        'xoxb-' 'xoxa-' 'xoxp-' 'xoxr-' 'xoxs-' \
        'AIza' \
        'sk_live_' 'rk_live_' \
        '-----BEGIN ' \
        '://'
}

# build_history_blobs: collect each unique blob once, with its first path for
# readable findings. Pipes through cat-file in the same order as rev-list.
build_history_blobs() {
    local objects="$workdir/history-objects"
    local types="$workdir/history-object-types"
    local sorted="$workdir/history-blobs"
    local type_line object_line oid typ path

    history_blob_paths="$workdir/history-blob-paths"
    candidate_blobs="$workdir/candidate-blobs"
    : >"$history_blob_paths"
    : >"$candidate_blobs"
    : >"$sorted"

    if ! git rev-list --objects --all >"$objects"; then
        echo "secret-scan: git rev-list failed while listing history objects" >&2
        exit 2
    fi
    if ! cut -d' ' -f1 "$objects" | git cat-file --batch-check >"$types"; then
        echo "secret-scan: git cat-file failed while typing history objects" >&2
        exit 2
    fi

    exec 3<"$objects"
    while IFS= read -r type_line; do
        IFS= read -r object_line <&3 || break
        oid="${type_line%% *}"
        typ="${type_line#* }"; typ="${typ%% *}"
        if [ "$typ" = "blob" ]; then
            path="${object_line#* }"
            if [ "$path" = "$object_line" ]; then path="$oid"; fi
            # Legacy dependency/cache blobs are not source and make a full-history
            # scan both noisy and too slow; source and config blobs are untouched.
            if excluded_path "$path"; then continue; fi
            printf '%s\n' "$oid" >>"$sorted"
            printf '%s %s\n' "$oid" "$path" >>"$history_blob_paths"
        fi
    done <"$types"
    exec 3<&-

    sort -u -o "$sorted" "$sorted"
    history_blobs=()
    while IFS= read -r oid; do
        if [ -n "$oid" ]; then history_blobs+=("$oid"); fi
    done <"$sorted"
}

blob_path() {
    local oid="$1" line path
    line="$(grep -m1 -F "$oid " "$history_blob_paths" 2>/dev/null || true)"
    if [ -z "$line" ]; then return 1; fi
    path="${line#* }"
    if [ -z "$path" ]; then return 1; fi
    printf '%s' "$path"
}

# record_blob_candidates: NUL-framed `blob-id \0 line \0 match \n` from `git grep`.
record_blob_candidates() {
    local oid line match rc
    while true; do
        oid=""; rc=0
        IFS= read -r -d '' oid || rc=$?
        if [ "$rc" -ne 0 ]; then
            if [ -n "$oid" ]; then
                printf '%s:0: truncated scanner record — content withheld\n' '?' >>"$findings_file"
            fi
            break
        fi
        line=""; rc=0
        IFS= read -r -d '' line || rc=$?
        if [ "$rc" -ne 0 ]; then
            printf '%s:0: truncated scanner record — content withheld\n' '?' >>"$findings_file"
            break
        fi
        match=""; rc=0
        IFS= read -r match || rc=$?
        if [ "$rc" -ne 0 ] && [ -z "$match" ]; then
            printf '%s:0: truncated scanner record — content withheld\n' '?' >>"$findings_file"
            break
        fi
        case "$line" in
            ''|*[!0-9]*) continue ;;
        esac
        printf '%s\n' "$oid" >>"$candidate_blobs"
    done
}

# record_blob_hits: same framing, but resolves the blob id to a path and writes
# an ordinary `path:line: label` finding.
record_blob_hits() {
    local scope="$1" label="$2"
    local oid line match rc path
    while true; do
        oid=""; rc=0
        IFS= read -r -d '' oid || rc=$?
        if [ "$rc" -ne 0 ]; then
            if [ -n "$oid" ]; then
                printf '%s:0: %s (truncated scanner record — content withheld)\n' '?' "$label" >>"$findings_file"
            fi
            break
        fi
        line=""; rc=0
        IFS= read -r -d '' line || rc=$?
        if [ "$rc" -ne 0 ]; then
            printf '%s:0: %s (truncated scanner record — content withheld)\n' '?' "$label" >>"$findings_file"
            break
        fi
        match=""; rc=0
        IFS= read -r match || rc=$?
        if [ "$rc" -ne 0 ] && [ -z "$match" ]; then
            printf '%s:0: %s (truncated scanner record — content withheld)\n' "$oid" "$label" >>"$findings_file"
            break
        fi
        case "$line" in
            ''|*[!0-9]*) continue ;;
        esac
        path="$(blob_path "$oid")" || path="$oid"
        if [ "$scope" = "url" ] && allowlisted "$path"; then continue; fi
        printf '%s:%s: %s\n' "$path" "$line" "$label" >>"$findings_file"
    done
}

scan_history_candidates() {
    local i=0 rc p
    local -a chunk specs
    while [ "$i" -lt "${#history_blobs[@]}" ]; do
        chunk=("${history_blobs[@]:i:BLOB_BATCH}")
        specs=()
        while IFS= read -r p; do
            if [ -n "$p" ]; then specs+=("-e" "$p"); fi
        done < <(emit_candidate_prefixes)
        rc=0
        git grep -F -a -n -o -z "${specs[@]}" "${chunk[@]}" \
            | record_blob_candidates || rc=$?
        if [ "$rc" -ge 2 ]; then
            echo "secret-scan: git grep candidate pass failed" >&2
            exit 2
        fi
        i=$((i + BLOB_BATCH))
    done
    sort -u -o "$candidate_blobs" "$candidate_blobs"
}

scan_candidate_patterns() {
    local scope="$1" label="$2" pattern="$3"
    local i=0 rc oid
    local -a chunk candidates=()
    while IFS= read -r oid; do
        if [ -n "$oid" ]; then candidates+=("$oid"); fi
    done <"$candidate_blobs"
    while [ "$i" -lt "${#candidates[@]}" ]; do
        chunk=("${candidates[@]:i:BLOB_BATCH}")
        rc=0
        git grep -E -a -n -o -z -e "$pattern" "${chunk[@]}" \
            | record_blob_hits "$scope" "$label" || rc=$?
        if [ "$rc" -ge 2 ]; then
            echo "secret-scan: git grep pattern pass failed while scanning for: $label" >&2
            exit 2
        fi
        i=$((i + BLOB_BATCH))
    done
}

# scan_commit_messages: `git log --grep` (POSIX ERE, subject AND body). The
# output is commit IDs only — the message itself is never echoed.
scan_commit_messages() {
    local scope label pattern shas rc sha
    while IFS=$'\t' read -r scope label pattern; do
        if [ -z "$label" ]; then
            continue
        fi
        rc=0
        shas="$(git log --all --extended-regexp --grep="$pattern" --format=%H)" || rc=$?
        if [ "$rc" -ne 0 ]; then
            echo "secret-scan: git log --grep failed (exit $rc) while scanning for: $label" >&2
            exit 2
        fi
        while IFS= read -r sha; do
            if [ -n "$sha" ]; then
                printf '%s:0: %s (commit message)\n' "$sha" "$label" >>"$findings_file"
            fi
        done <<<"$shas"
    done < <(emit_patterns)
}

# scan_tag_messages: annotated-tag messages (lightweight tags have none — their
# %(contents) is the pointed-to commit message, already covered above). Stream
# is NUL-framed per field: name\0type\0contents\0 + a newline between entries.
scan_tag_messages() {
    local tag_stream="$workdir/tag-messages"
    local name typ body scope label pattern rc
    if ! git for-each-ref refs/tags \
            --format='%(refname:short)%00%(objecttype)%00%(contents)%00' \
            >"$tag_stream"; then
        echo "secret-scan: git for-each-ref failed while listing tags" >&2
        exit 2
    fi
    while IFS= read -r -d '' name; do
        # for-each-ref terminates each entry with a newline; it lands on the
        # next name field. Stripping it is a no-op for the first entry.
        name="${name#$'\n'}"
        typ=""; rc=0
        if ! IFS= read -r -d '' typ; then
            printf 'refs/tags/%s:0: (truncated tag record — content withheld)\n' \
                "${name:-?}" >>"$findings_file"
            break
        fi
        body=""; rc=0
        if ! IFS= read -r -d '' body; then
            printf 'refs/tags/%s:0: (truncated tag message — content withheld)\n' \
                "$name" >>"$findings_file"
            break
        fi
        if [ "$typ" != "tag" ]; then
            continue
        fi
        while IFS=$'\t' read -r scope label pattern; do
            if [ -z "$label" ]; then
                continue
            fi
            rc=0
            printf '%s\n' "$body" | grep -Eaq -e "$pattern" || rc=$?
            if [ "$rc" -eq 0 ]; then
                printf 'refs/tags/%s:0: %s (annotated tag message)\n' \
                    "$name" "$label" >>"$findings_file"
            elif [ "$rc" -ge 2 ]; then
                echo "secret-scan: grep failed (exit $rc) while scanning tag $name for: $label" >&2
                exit 2
            fi
        done < <(emit_patterns)
    done <"$tag_stream"
}

if [ "$MODE" = "--staged" ] && [ "${#staged_paths[@]}" -gt 0 ]; then
    while IFS=$'\t' read -r scope label pattern; do
        if [ -z "$label" ]; then continue; fi
        scan_index "$scope" "$label" "$pattern"
    done < <(emit_patterns)
elif [ "$MODE" = "--tracked" ] && [ "$has_head" -eq 1 ] && [ "${#tree_paths[@]}" -gt 0 ]; then
    while IFS=$'\t' read -r scope label pattern; do
        if [ -z "$label" ]; then continue; fi
        scan_revs "$scope" "$label" "$pattern" HEAD
    done < <(emit_patterns)
elif [ "$MODE" = "--files" ] && [ "${#scan_files[@]}" -gt 0 ]; then
    while IFS=$'\t' read -r scope label pattern; do
        if [ -z "$label" ]; then continue; fi
        scan_worktree_files "$scope" "$label" "$pattern"
    done < <(emit_patterns)
elif [ "$MODE" = "--history" ] && [ "${#commits[@]}" -gt 0 ]; then
    build_history_blobs
    if [ "${#history_blobs[@]}" -gt 0 ]; then
        scan_history_candidates
        while IFS=$'\t' read -r scope label pattern; do
            if [ -z "$label" ]; then continue; fi
            scan_candidate_patterns "$scope" "$label" "$pattern"
        done < <(emit_patterns)
    fi
    scan_commit_messages
fi

if [ "$MODE" = "--history" ]; then
    scan_tag_messages
fi

# ---------------------------------------------------------------------------
# 3) Report.
# ---------------------------------------------------------------------------
pattern_count="$(emit_patterns | wc -l | tr -d '[:space:]')"

case "$MODE" in
    --staged)  scope="${#staged_paths[@]} file(s) staged for commit" ;;
    --tracked) scope="${#tree_paths[@]} tracked file(s) at HEAD" ;;
    --files)   scope="${#scan_files[@]} of ${#files[@]} file(s)" ;;
    --history) scope="${#commits[@]} commit(s) of history" ;;
esac

if [ -s "$findings_file" ]; then
    sort -u -o "$findings_file" "$findings_file"
    cat "$findings_file"
    n="$(wc -l <"$findings_file" | tr -d '[:space:]')"
    cat <<EOF

secret-scan: BLOCKED — $n finding(s) in $scope. This blocks the commit/push.

  The Amalthea repository is PUBLIC and git history is permanent: anything
  committed is world-readable, and deleting the file later does NOT remove the
  blob from history.

  How to fix:
    1. REMOVE the secret from the file — never commit it.
    2. ROTATE it at the provider immediately; treat it as already leaked.
    3. Keep the replacement in an environment variable or a local .env file
       (gitignored; .env.example shows the format with placeholders only).
    4. Already reached a commit? Rotate first, then purge history — see SECURITY.md.

  Each finding above is <file>:<line>: <label> — the matched secret itself is
  never printed. Line 0 means the file name (or tag name) itself is the
  violation; "(commit message)" and "(annotated tag message)" mark matches
  outside file content.
EOF
    exit 1
fi

printf 'secret-scan: clean — no secrets in %s (%s patterns, mode %s)\n' \
    "$scope" "$pattern_count" "$MODE"
exit 0

#!/usr/bin/env bash
# Install Amalthea's git hooks. `.git/hooks/` is not tracked, so every clone must run this.
# `make hooks` wraps it.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [ ! -d .git ]; then
    echo "install-hooks: not a git repository" >&2
    exit 1
fi

mkdir -p .git/hooks
for hook in pre-commit pre-push; do
    src="scripts/$hook.sh"
    dst=".git/hooks/$hook"
    [ -f "$src" ] || { echo "install-hooks: missing $src" >&2; exit 1; }
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "  installed $dst"
done

echo ""
echo "✓ hooks installed. They run automatically on commit and push."

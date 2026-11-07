"""JSON tree builder for the "raw-data click-to-map" feature.

Pure, testable functions that turn a payload dict/list into a navigable tree
of JsonNode objects, with JSONPath generation and truncation controls.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jsonpath_ng import parse as _jsonpath_parse

from ingest.mapping import MAX_PATH_LEN

MAX_TREE_NODES: int = 1000
MAX_TREE_DEPTH: int = 12
MAX_VALUE_CHARS: int = 200


def jsonpath_for(parts: Sequence[str | int]) -> str:
    r"""Build a JSONPath string from a sequence of key/index parts.

    - Keys matching ``^[A-Za-z_][A-Za-z0-9_]*$`` become ``.identifier``.
    - Integer parts become ``[n]``.
    - Everything else becomes ``["..."]`` with ``\`` and ``"`` escaped.
    The result must round-trip through ``jsonpath_ng.parse(...).find(payload)``.
    """
    if not parts:
        return "$"
    components: list[str] = ["$"]
    for part in parts:
        if isinstance(part, int):
            components.append(f"[{part}]")
        elif part.isidentifier() and not part[0].isdigit():
            # matches ^[A-Za-z_][A-Za-z0-9_]*$
            components.append(f".{part}")
        else:
            # escaped: backslash and double-quote escape
            escaped = str(part).replace("\\", "\\\\").replace('"', '\\"')
            components.append(f'["{escaped}"]')
    return "".join(components)


def all_items_path(parts: Sequence[str | int]) -> str:
    """Like ``jsonpath_for`` but every integer index becomes ``[*]``.

    Returns the concrete path when there are no indices; otherwise a path
    with ``[*]`` wildcards for array positions.
    """
    if not parts:
        return "$"
    components: list[str] = ["$"]
    for part in parts:
        if isinstance(part, int):
            components.append("[*]")
        elif part.isidentifier() and not part[0].isdigit():
            components.append(f".{part}")
        else:
            escaped = str(part).replace("\\", "\\\\").replace('"', '\\"')
            components.append(f'["{escaped}"]')
    return "".join(components)


def is_valid_jsonpath(path: str) -> bool:
    """Return True if *path* is a non-empty JSONPath that starts with ``$``,
    respects ``MAX_PATH_LEN``, and compiles under ``jsonpath_ng``."""
    stripped = path.strip()
    if not stripped or not stripped.startswith("$"):
        return False
    if len(stripped) > MAX_PATH_LEN:
        return False
    try:
        _jsonpath_parse(stripped)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class JsonNode:
    """One node in a JSON tree produced by ``build_json_tree``."""

    key: str
    """Dict key, list index as str, or "" for scalar root."""
    kind: str
    """"object" | "array" | "scalar"."""
    value_repr: str
    """Scalars: ``json.dumps(value, ensure_ascii=False)`` truncated to
    ``MAX_VALUE_CHARS`` + "..."; containers: ""."""
    path: str
    """JSONPath to this node (e.g. ``$.a[0].b``)."""
    all_path: str
    """JSONPath with ``[*]`` wildcards for any array indices."""
    children: tuple[JsonNode, ...] = ()
    """Child nodes; always empty for kind == "scalar"."""
    truncated: bool = False
    """True when the node was capped by ``max_nodes`` or ``max_depth``."""


@dataclass(frozen=True)
class JsonTree:
    """An immutable tree of ``JsonNode`` objects."""

    nodes: tuple[JsonNode, ...]
    truncated: bool
    """True when any node hit a cap."""


def build_json_tree(
    value: Any,
    *,
    max_nodes: int = MAX_TREE_NODES,
    max_depth: int = MAX_TREE_DEPTH,
) -> JsonTree:
    """Turn a JSON payload into a navigable ``JsonTree`` of ``JsonNode`` objects.

    *dict* → one node per key; *list* → one node per index; *scalar* → one node
    with ``key=""``.  Counts nodes, stops at the caps and sets ``truncated=True``.
    Never raises on odd payloads.

    The tree is built depth-first.  A mutable counter tracks how many nodes have
    been placed; once ``max_nodes`` nodes are placed no further children are
    created and the tree is marked truncated.  Similarly, descending beyond
    ``max_depth`` stops and the current node is marked a scalar leaf.
    """
    nodes: list[JsonNode] = []
    # mutable counter shared across recursive calls
    placed: list[int] = [0]
    # mutable flag: True if any cap was hit
    hit_cap: list[bool] = [False]

    _build(value, max_nodes, max_depth, 0, [], nodes, placed, hit_cap)

    return JsonTree(nodes=tuple(nodes), truncated=hit_cap[0])


def _build(
    value: Any,
    max_nodes: int,
    max_depth: int,
    current_depth: int,
    prefix_parts: list[str | int],
    nodes: list[JsonNode],
    placed: list[int],
    hit_cap: list[bool],
) -> None:
    """Recursive builder that respects ``max_nodes`` and ``max_depth``.

    ``placed[0]` is the number of nodes already appended to ``nodes``.
    After appending a node we do ``placed[0] += 1``; if it reaches ``max_nodes``
    we set ``hit_cap[0] = True`` and stop descending.
    """
    # --- scalar root (the initial call with a non-dict/non-list value) ---
    if not isinstance(value, (dict, list)):
        # one scalar node with empty key
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        s = json.dumps(value, ensure_ascii=False)
        value_repr = s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."
        node = JsonNode(
            key="",
            kind="scalar",
            value_repr=value_repr,
            path=path,
            all_path=all_path,
            children=(),
            truncated=False,
        )
        nodes.append(node)
        placed[0] += 1
        if placed[0] >= max_nodes:
            hit_cap[0] = True
        return

    # --- depth check: entering at current_depth + 1 ---
    new_depth = current_depth + 1
    if new_depth > max_depth:
        # can't descend; treat as scalar leaf
        s = json.dumps(value, ensure_ascii=False)
        value_repr = s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        node = JsonNode(
            key="",
            kind="scalar",
            value_repr=value_repr,
            path=path,
            all_path=all_path,
            children=(),
            truncated=new_depth > max_depth,
        )
        nodes.append(node)
        placed[0] += 1
        hit_cap[0] = True  # depth cap was hit
        return

    # --- count this node ---
    placed[0] += 1
    if placed[0] >= max_nodes:
        hit_cap[0] = True
        # Still append the node (it's the last one), but don't descend further
        kind = "object" if isinstance(value, dict) else "array"
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        node = JsonNode(
            key="",
            kind=kind,
            value_repr="",
            path=path,
            all_path=all_path,
            children=(),
            truncated=True,
        )
        nodes.append(node)
        return

    # --- dict or list ---
    if isinstance(value, dict):
        kind = "object"
    else:
        kind = "array"

    parts = list(prefix_parts)
    path = jsonpath_for(parts) if parts else "$"
    all_path = all_items_path(parts) if parts else "$"

    # Append this node
    node = JsonNode(
        key="",
        kind=kind,
        value_repr="",
        path=path,
        all_path=all_path,
        children=(),
        truncated=False,
    )
    nodes.append(node)

    # Descend into children, stopping if cap reached
    if isinstance(value, dict):
        for key, val in value.items():
            if placed[0] >= max_nodes:
                hit_cap[0] = True
                break
            _build(
                val, max_nodes, max_depth, new_depth, [*prefix_parts, key], nodes, placed, hit_cap
            )
    elif isinstance(value, list):
        for idx, val in enumerate(value):
            if placed[0] >= max_nodes:
                hit_cap[0] = True
                break
            _build(
                val, max_nodes, max_depth, new_depth, [*prefix_parts, idx], nodes, placed, hit_cap
            )

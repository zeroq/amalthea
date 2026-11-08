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
    """""object" | "array" | "scalar"."""
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
    is_index: bool = False
    """True if this node's key comes from a list index position."""
    count: int = 0
    """Dict size / list length for containers; 0 for scalars."""


@dataclass(frozen=True)
class JsonTree:
    """An immutable tree of ``JsonNode`` objects."""

    nodes: tuple[JsonNode, ...]
    truncated: bool
    """True when any node hit a cap."""


def _derive_key_isindex(prefix_parts: list[str | int]) -> tuple[str, bool]:
    """Derive ``key`` and ``is_index`` from *prefix_parts*.

    - Empty prefix → root: key="", is_index=False.
    - Last part is int → key=str(idx), is_index=True (list position).
    - Last part is str → key=str(key), is_index=False (dict key).
    """
    if not prefix_parts:
        return ("", False)
    last = prefix_parts[-1]
    if isinstance(last, int):
        return (str(last), True)
    return (str(last), False)


def _build(
    value: Any,
    max_nodes: int,
    max_depth: int,
    current_depth: int,
    prefix_parts: list[str | int],
    placed: list[int],
    hit_cap: list[bool],
) -> JsonNode:
    """Recursive builder that returns a ``JsonNode``.

    Children are attached to the returned node via the ``children`` field.
    ``JsonTree.nodes`` contains only the top-level root node (normally one root;
    scalar roots still one node with ``key=""``).
    """
    # --- derive key / is_index from prefix_parts ---
    key, is_index = _derive_key_isindex(prefix_parts)

    # --- scalar value (leaf) ---
    if not isinstance(value, (dict, list)):
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        s = json.dumps(value, ensure_ascii=False)
        value_repr = s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."
        node = JsonNode(
            key=key,
            kind="scalar",
            value_repr=value_repr,
            path=path,
            all_path=all_path,
            children=(),
            truncated=False,
            is_index=is_index,
            count=0,
        )
        placed[0] += 1
        if placed[0] >= max_nodes:
            hit_cap[0] = True
        return node

    # --- depth check: entering at current_depth + 1 ---
    new_depth = current_depth + 1
    if new_depth > max_depth:
        s = json.dumps(value, ensure_ascii=False)
        value_repr = s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "..."
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        node = JsonNode(
            key=key,
            kind="scalar",
            value_repr=value_repr,
            path=path,
            all_path=all_path,
            children=(),
            truncated=new_depth > max_depth,
            is_index=is_index,
            count=0,
        )
        placed[0] += 1
        hit_cap[0] = True  # depth cap was hit
        return node

    # --- count this node ---
    placed[0] += 1
    if placed[0] >= max_nodes:
        hit_cap[0] = True
        kind = "object" if isinstance(value, dict) else "array"
        parts = list(prefix_parts)
        path = jsonpath_for(parts) if parts else "$"
        all_path = all_items_path(parts) if parts else "$"
        node = JsonNode(
            key=key,
            kind=kind,
            value_repr="",
            path=path,
            all_path=all_path,
            children=(),
            truncated=True,
            is_index=is_index,
            count=len(value),
        )
        return node

    # --- dict or list: build children ---
    if isinstance(value, dict):
        kind = "object"
    else:
        kind = "array"

    parts = list(prefix_parts)
    path = jsonpath_for(parts) if parts else "$"
    all_path = all_items_path(parts) if parts else "$"

    # Build children tuple; track if we stopped early due to max_nodes
    children: list[JsonNode] = []
    stopped_early = False

    if isinstance(value, dict):
        for dict_key, val in value.items():
            if placed[0] >= max_nodes:
                stopped_early = True
                hit_cap[0] = True
                break
            child = _build(
                val, max_nodes, max_depth, new_depth, [*prefix_parts, dict_key], placed, hit_cap
            )
            children.append(child)
    elif isinstance(value, list):
        for idx, val in enumerate(value):
            if placed[0] >= max_nodes:
                stopped_early = True
                hit_cap[0] = True
                break
            child = _build(
                val, max_nodes, max_depth, new_depth, [*prefix_parts, idx], placed, hit_cap
            )
            children.append(child)

    count = len(value)  # actual container size, even if truncated

    node = JsonNode(
        key=key,
        kind=kind,
        value_repr="",
        path=path,
        all_path=all_path,
        children=tuple(children),
        truncated=stopped_early,
        is_index=is_index,
        count=count,
    )
    return node


def build_json_tree(
    value: Any,
    *,
    max_nodes: int = MAX_TREE_NODES,
    max_depth: int = MAX_TREE_DEPTH,
) -> JsonTree:
    """Turn a JSON payload into a navigable ``JsonTree`` of ``JsonNode`` objects.

    *dict* → one root node per key; *list* → one root node per index; *scalar* → one
    root node with ``key=""``.  Counts nodes, stops at the caps and sets
    ``truncated=True``.  Never raises on odd payloads.

    The tree is built depth-first.  A mutable counter tracks how many nodes have
    been placed; once ``max_nodes`` nodes are placed no further children are
    created and the tree is marked truncated.  Similarly, descending beyond
    ``max_depth`` stops and the current node is marked a scalar leaf.

    The returned ``JsonTree.nodes`` tuple contains the single root node; children
    are reachable via the root node's ``children`` field.
    """
    placed: list[int] = [0]
    hit_cap: list[bool] = [False]
    root = _build(value, max_nodes, max_depth, 0, [], placed, hit_cap)
    return JsonTree(nodes=(root,), truncated=hit_cap[0])

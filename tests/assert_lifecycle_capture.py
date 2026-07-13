#!/usr/bin/env python3
"""Assert Windows routing in a real Codex model-request capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROUTE_MARKERS = (
    "WINDOWS COMMAND ROUTE (same-turn bundled context)",
    "--- BEGIN PLUGIN-OWNED SKILL.md ---",
    "WINDOWS COMMAND ROUTING DEGRADED",
)
EXPECTED_PROMPTS = {
    "active": "Run PowerShell Get-Process on this Windows host.",
    "silent": "Explain this Linux shell command.",
}


def item_text(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    content = item.get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-file", type=Path, required=True)
    parser.add_argument("--request-index", type=int, required=True)
    parser.add_argument("--expect", choices=("active", "silent"), required=True)
    args = parser.parse_args()
    requests = [
        json.loads(line)
        for line in args.capture_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.request_index >= len(requests):
        raise SystemExit(
            f"missing request index {args.request_index}; captured {len(requests)}"
        )
    request = requests[args.request_index]
    inputs = request.get("input", []) if isinstance(request, dict) else []
    if not isinstance(inputs, list):
        raise SystemExit("captured request has no input list")
    user_texts = [
        item_text(item)
        for item in inputs
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    if EXPECTED_PROMPTS[args.expect] not in user_texts:
        raise SystemExit("captured request is missing the exact user prompt")
    developer_texts = [
        item_text(item)
        for item in inputs
        if isinstance(item, dict) and item.get("role") == "developer"
    ]
    serialized = json.dumps(developer_texts, separators=(",", ":"))
    if args.expect == "silent":
        found = [marker for marker in ROUTE_MARKERS if marker in serialized]
        if found:
            raise SystemExit(f"unrelated prompt received Windows routing: {found}")
        return 0
    for marker in ROUTE_MARKERS[:2]:
        if marker not in serialized:
            raise SystemExit(f"real Codex request is missing Windows hook marker: {marker}")
    if ROUTE_MARKERS[2] in serialized:
        raise SystemExit("real Codex lifecycle degraded instead of loading the skill")
    if "Never use encoded or obfuscated command transport" not in serialized:
        raise SystemExit("real Codex request is missing the transport safety rule")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

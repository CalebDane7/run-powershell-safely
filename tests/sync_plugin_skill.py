#!/usr/bin/env python3
"""Synchronize or verify the plugin skill mirror against the standalone skill."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "skills" / "run-powershell-safely"
PLUGIN_ROOT = REPO_ROOT / "plugins" / "run-powershell-safely"
TARGET = PLUGIN_ROOT / "skills" / "run-powershell-safely"


def _inventory(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"symlink is not allowed: {path.relative_to(root)}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _assert_safe_target() -> None:
    expected = (PLUGIN_ROOT / "skills" / "run-powershell-safely").resolve()
    if TARGET.resolve() != expected or TARGET == SOURCE:
        raise RuntimeError("refusing to modify an unexpected skill mirror path")


def sync() -> int:
    _assert_safe_target()
    if TARGET.exists():
        # WHY: This exact directory is generated from the canonical standalone
        # skill; replacing it prevents stale files from surviving a sync.
        shutil.rmtree(TARGET)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        SOURCE,
        TARGET,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        copy_function=shutil.copy2,
    )
    print(f"synced {len(_inventory(TARGET))} files")
    return 0


def check() -> int:
    source = _inventory(SOURCE)
    target = _inventory(TARGET) if TARGET.is_dir() else {}
    if source != target:
        missing = sorted(source.keys() - target.keys())
        extra = sorted(target.keys() - source.keys())
        changed = sorted(
            path for path in source.keys() & target.keys() if source[path] != target[path]
        )
        print(
            f"plugin skill mirror mismatch: missing={missing} extra={extra} "
            f"changed={changed}",
            file=sys.stderr,
        )
        return 1
    print(f"plugin skill mirror matches {len(source)} files")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())

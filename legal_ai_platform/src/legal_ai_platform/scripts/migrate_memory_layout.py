"""One-time migration from flat memory/ layout to scoped tenant/user paths."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from deep_research_from_scratch.memory_namespace import MemoryNamespace, resolve_memory_paths
from deep_research_from_scratch.memory_tools import get_memory_root


def migrate_memory_root(memory_root: Path) -> int:
    """Move legacy flat sessions/auto into tenants/_legacy/users/_unknown/."""
    moved = 0
    legacy_ns = MemoryNamespace(tenant_id="_legacy", user_id="_unknown")
    target = resolve_memory_paths(legacy_ns, memory_root=memory_root)

    flat_sessions = memory_root / "sessions"
    if flat_sessions.is_dir():
        target.sessions_dir.mkdir(parents=True, exist_ok=True)
        for path in flat_sessions.iterdir():
            if path.is_file():
                dest = target.sessions_dir / path.name
                if not dest.exists():
                    shutil.move(str(path), str(dest))
                    moved += 1
        if not any(flat_sessions.iterdir()):
            flat_sessions.rmdir()

    flat_auto = memory_root / "auto"
    if flat_auto.is_dir():
        target.auto_dir.mkdir(parents=True, exist_ok=True)
        for path in flat_auto.iterdir():
            if path.is_file():
                dest = target.auto_dir / path.name
                if not dest.exists():
                    shutil.move(str(path), str(dest))
                    moved += 1
        if not any(flat_auto.iterdir()):
            flat_auto.rmdir()

    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate flat memory files to scoped layout")
    parser.add_argument(
        "--memory-root",
        default=None,
        help="Memory root (defaults to DEEP_RESEARCH_MEMORY_DIR or ./memory)",
    )
    args = parser.parse_args()
    root = Path(args.memory_root) if args.memory_root else get_memory_root()
    count = migrate_memory_root(root)
    print(f"Migrated {count} file(s) under {root}")


if __name__ == "__main__":
    main()

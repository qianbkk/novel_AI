"""Remove legacy placeholder/title lines from explicitly selected chapter files.

The command is a dry run unless ``--apply`` is supplied. It never assumes a
project or output directory; callers must name every chapter directory.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path


DEFAULT_CHAPTERS = (1, 42, 50)


def looks_like_junk_header(line: str) -> bool:
    """Return whether a leading line is a known placeholder or duplicate title."""
    text = line.strip()
    if not text:
        return False
    if any(word in text for word in ("修改后正文", "smoke", "TODO", "FIXME", "测试稿", "scaffold")):
        return True
    if re.match(r"^【[^】]+】第\d+章", text):
        return True
    return bool(re.match(r"^第\d+章\s*\S+", text) and len(text) <= 30)


def clean_file(path: Path, *, apply: bool = False) -> tuple[int, list[str]]:
    """Inspect one file and optionally remove all matching leading lines."""
    if not path.exists():
        return 0, []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    removed: list[str] = []
    index = 0
    while index < len(lines) and looks_like_junk_header(lines[index]):
        removed.append(lines[index].rstrip("\r\n"))
        index += 1
    if removed and apply:
        path.write_text("".join(lines[index:]), encoding="utf-8")
    return len(removed), removed


def _parse_chapters(value: str) -> tuple[int, ...]:
    try:
        chapters = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chapters must be comma-separated integers") from exc
    if not chapters or any(chapter < 1 for chapter in chapters):
        raise argparse.ArgumentTypeError("chapters must contain positive integers")
    return chapters


def _reimport(project_id: str) -> int:
    from app.bridge.chapter_import import _force_reimport
    from app.database import SessionLocal
    from app.models import NovelAIBinding

    db = SessionLocal()
    try:
        binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
        if binding is None:
            raise RuntimeError(f"project {project_id!r} has no engine binding")
        return len(asyncio.run(_force_reimport(project_id, binding.novel_ai_dir, db)))
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chapters-dir",
        action="append",
        required=True,
        type=Path,
        help="chapter directory to inspect; repeat for multiple output trees",
    )
    parser.add_argument(
        "--chapters",
        type=_parse_chapters,
        default=DEFAULT_CHAPTERS,
        help="comma-separated chapter numbers (default: 1,42,50)",
    )
    parser.add_argument("--apply", action="store_true", help="write changes; default is dry-run")
    parser.add_argument(
        "--reimport-project",
        help="after --apply, reimport the explicitly named project from its binding",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.reimport_project and not args.apply:
        build_parser().error("--reimport-project requires --apply")

    total = 0
    for directory in args.chapters_dir:
        directory = directory.resolve()
        for chapter in args.chapters:
            path = directory / f"ch_{chapter:04d}.txt"
            count, removed = clean_file(path, apply=args.apply)
            total += count
            status = "changed" if args.apply and count else "would-change" if count else "clean"
            print(f"[{status}] {path} ({count} line(s))")
            for line in removed:
                print(f"  {line!r}")

    if args.reimport_project:
        imported = _reimport(args.reimport_project)
        print(f"[reimported] project={args.reimport_project} chapters={imported}")
    print(f"total={'changed' if args.apply else 'would-change'}:{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

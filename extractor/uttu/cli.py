"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import config, site
from .pipeline import JobManager, manager


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_parts(value: str) -> list[int]:
    result: list[int] = []
    for chunk in value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            left, right = chunk.split("-", 1)
            result.extend(range(int(left), int(right) + 1))
        else:
            result.append(int(chunk))
    return result


def cmd_scan(args) -> int:
    tree = site.get_tree(refresh=args.refresh)
    total = sum(len(category["chapters"]) for category in tree["categories"])
    print(f"Categories: {len(tree['categories'])}  Chapters: {total}")
    for category in tree["categories"]:
        print(f"  {category['label']:<18} {len(category['chapters']):>3} chapters  ({category['slug']})")
    for error in tree["errors"]:
        print(f"  ERROR {error['category']}: {error['error']}", file=sys.stderr)
    return 0


def cmd_tree(args) -> int:
    tree = site.get_tree(refresh=args.refresh)
    for category in tree["categories"]:
        print(f"{category['label']} [{category['slug']}]")
        for chapter in category["chapters"]:
            print(f"  {chapter['path']:<48} {chapter['title_en']}")
    return 0


def cmd_chapter(args) -> int:
    if args.path:
        parts = args.path.strip("/").split("/")
        if len(parts) != 3:
            print("--path must look like /story/main/chapter-0/", file=sys.stderr)
            return 2
        category, slug = parts[1], parts[2]
    else:
        category, slug = args.category, args.slug
    chapter = site.fetch_chapter(category, slug, refresh=args.refresh)
    print(f"{chapter.title_en} / {chapter.title_zh}  [{chapter.label}]")
    print(f"recording: {chapter.recording_url}")
    print(f"parts: {len(chapter.parts)}")
    for part in chapter.parts:
        print(
            f"  {part.number:>3}  {part.title_en:<40}  {part.title_zh:<24}  "
            f"EN {len(part.content_en):>6} ZH {len(part.content_zh):>6}"
        )
    for warning in chapter.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


def cmd_extract(args) -> int:
    chapter_path = args.chapter
    parts = _parse_parts(args.parts) if args.parts else []
    chapter = site.fetch_chapter(*chapter_path.strip("/").split("/")[1:3])
    available = {part.number for part in chapter.parts}
    if args.all:
        parts = sorted(available)
    if not parts:
        print("Select Parts with --parts 1,2,3 or --all.", file=sys.stderr)
        return 2
    missing = [number for number in parts if number not in available]
    if missing:
        print(f"Parts not found: {missing}. Available: {sorted(available)}", file=sys.stderr)
        return 2
    selection = [{"path": chapter.path, "number": number} for number in parts]
    job = manager.create(
        selection,
        {
            "force": args.force,
            "force_media": args.force_media,
            "max_height": args.height,
        },
    )
    print(f"Job {job.id} started with {len(selection)} Part(s).")
    last_log = 0
    while True:
        snapshot = job.snapshot()
        for entry in snapshot["logs"][last_log:]:
            print(f"[{entry['time']}] {entry['message']}")
        last_log = len(snapshot["logs"])
        if snapshot["status"] in ("done", "partial", "failed", "cancelled"):
            print(json.dumps(
                {
                    "status": snapshot["status"],
                    "outputs": [
                        {"part": item["number"], "dir": item["dir"], "cues": item["cues"]}
                        for item in snapshot["outputs"]
                    ],
                    "errors": snapshot["errors"],
                    "report": snapshot["report_path"],
                },
                ensure_ascii=False,
                indent=1,
            ))
            if args.wait:
                return 0 if snapshot["status"] in ("done", "partial") else 1
            return 0
        time.sleep(0.6)


def cmd_validate(args) -> int:
    from .validate import validate_tree, write_report

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"Output root does not exist: {root}", file=sys.stderr)
        return 2
    report = validate_tree(root, decode=args.decode, frames=not args.no_frames)
    output = Path(args.out) if args.out else root / "validation.json"
    write_report(report, output)
    print(
        f"Checked {report['parts_checked']} Part(s), {report['cues_checked']} cues: "
        f"{'PASS' if report['ok'] else 'FAIL'} "
        f"({report['parts_failed']} failed)"
    )
    for item in report["parts"]:
        if not item["ok"]:
            for error in item["errors"]:
                print(f"  FAIL {Path(item['dir']).name}: {error}", file=sys.stderr)
    print(f"Report: {output}")
    return 0 if report["ok"] else 1


def cmd_manifest(args) -> int:
    from .manifest import write_manifest

    root = Path(args.root).resolve()
    target = write_manifest(root, Path(args.out) if args.out else None)
    print(f"Manifest: {target}")
    return 0


def cmd_refresh_subtitles(args) -> int:
    import time as _time

    from .refresh import refresh_tree, write_report

    root = Path(args.root).resolve()
    report = refresh_tree(root, limit=args.limit)
    target = Path(args.out) if args.out else (
        root / "reports" / f"subtitle-refresh-{_time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    write_report(report, target)
    print(
        f"Refreshed {report['checked']} Part(s): {report['changed_parts']} changed, "
        f"{report['changed_cues']} cues, {report['failed']} failed."
    )
    for part in report["parts"]:
        if not part["ok"]:
            print(f"  FAIL {part['dir']}: {part['error']}", file=sys.stderr)
    print(f"Report: {target}")
    return 0 if report["ok"] else 1


def cmd_serve(args) -> int:
    from .server import serve

    serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uttu", description="UTTU story resource extractor")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="scan categories and chapter lists")
    scan.add_argument("--refresh", action="store_true")
    scan.set_defaults(func=cmd_scan)

    tree = sub.add_parser("tree", help="print the category/chapter tree")
    tree.add_argument("--refresh", action="store_true")
    tree.set_defaults(func=cmd_tree)

    chapter = sub.add_parser("chapter", help="show a chapter and its Parts")
    chapter.add_argument("--path", help="e.g. /story/main/chapter-0/")
    chapter.add_argument("--category", default="main")
    chapter.add_argument("--slug", default="chapter-0")
    chapter.add_argument("--refresh", action="store_true")
    chapter.set_defaults(func=cmd_chapter)

    extract = sub.add_parser("extract", help="extract selected Parts")
    extract.add_argument("--chapter", required=True, help="e.g. /story/main/chapter-0/")
    extract.add_argument("--parts", help="comma/range list, e.g. 1,2,5-6")
    extract.add_argument("--all", action="store_true", help="extract every Part in the chapter")
    extract.add_argument("--force", action="store_true", help="replace existing output")
    extract.add_argument("--force-media", action="store_true", help="re-download the recording")
    extract.add_argument(
        "--height",
        type=int,
        default=config.VIDEO_MAX_HEIGHT,
        choices=[720, 1080, 1440, 2160],
        help="maximum video height (default: 1080, 1440=2K, 2160=4K)",
    )
    extract.add_argument("--wait", action="store_true", default=True, help=argparse.SUPPRESS)
    extract.set_defaults(func=cmd_extract)

    serve = sub.add_parser("serve", help="start the local web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    serve.set_defaults(func=cmd_serve)

    validate = sub.add_parser("validate", help="independently validate extracted output")
    validate.add_argument("--root", default=str(config.OUTPUT_DIR))
    validate.add_argument("--out", help="report path (default: <root>/validation.json)")
    validate.add_argument("--decode", action="store_true", help="fully decode every video")
    validate.add_argument("--no-frames", action="store_true", help="skip source frame comparison")
    validate.set_defaults(func=cmd_validate)

    refresh = sub.add_parser(
        "refresh-subtitles",
        help="re-align existing Parts and rewrite SRTs without re-encoding video",
    )
    refresh.add_argument("--root", default=str(config.OUTPUT_DIR))
    refresh.add_argument("--out", help="report path")
    refresh.add_argument("--limit", type=int, help="only process the first N Parts")
    refresh.set_defaults(func=cmd_refresh_subtitles)

    manifest = sub.add_parser("manifest", help="write SHA256 manifest for extracted files")
    manifest.add_argument("--root", default=str(config.OUTPUT_DIR))
    manifest.add_argument("--out", help="manifest path (default: <root>/MANIFEST.json)")
    manifest.set_defaults(func=cmd_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    config.ensure_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

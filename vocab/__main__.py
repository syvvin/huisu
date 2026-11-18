"""Command line entry point: python -m vocab [serve|sync|dict|stats]."""
from __future__ import annotations

import argparse
import json
import sys

from . import db, library, server


def cmd_serve(args) -> int:
    server.serve(host=args.host, port=args.port, open_browser=args.open,
                 sync_first=not args.no_sync)
    return 0


def cmd_sync(args) -> int:
    db.init_db()
    result = library.sync()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def cmd_stats(_args) -> int:
    db.init_db()
    print(json.dumps(library.totals(), ensure_ascii=False, indent=1))
    return 0


def cmd_dict(args) -> int:
    from . import dicts
    db.init_db()
    payload = dicts.lookup(args.word, None)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


def cmd_candidates(args) -> int:
    db.init_db()
    items = library.candidates(args.part_id)
    for item in items:
        print(f"{item['score']:>6}  {item['lemma']:<20} {item['brief_zh'][:40]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vocab", description="回溯 · 视频精读词卡")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动本地网页应用")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.add_argument("--open", action="store_true", help="启动后打开浏览器")
    p_serve.add_argument("--no-sync", action="store_true", help="跳过启动时的片库同步")
    p_serve.set_defaults(func=cmd_serve)

    p_sync = sub.add_parser("sync", help="重新扫描 uttu-extractor 输出目录")
    p_sync.set_defaults(func=cmd_sync)

    p_stats = sub.add_parser("stats", help="查看词库统计")
    p_stats.set_defaults(func=cmd_stats)

    p_dict = sub.add_parser("dict", help="命令行查词（调试用）")
    p_dict.add_argument("word")
    p_dict.set_defaults(func=cmd_dict)

    p_cand = sub.add_parser("candidates", help="查看某 Part 的生词候选")
    p_cand.add_argument("part_id", type=int)
    p_cand.set_defaults(func=cmd_candidates)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""AML 记忆服务 — 合规运维（30 天内删除评测数据）

赛事要求：评测数据仅用于评测、禁止训练、**评测结束后 30 天内删除**。
本脚本提供按 user 的删除与核对。

用法::

    python deploy/aml/compliance_purge.py --list
    python deploy/aml/compliance_purge.py --user <user_id>
    python deploy/aml/compliance_purge.py --all --confirm

默认数据库: ~/.laap/aml_memory.sqlite3（可用 --db 覆盖）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from laap.aml.service import AMLMemoryService


def main() -> int:
    ap = argparse.ArgumentParser(description="AML 合规运维")
    ap.add_argument("--db", default=os.environ.get("AML_DB", ""))
    ap.add_argument("--user", default="", help="按 user_id 删除")
    ap.add_argument("--all", action="store_true", help="删除全部（需 --confirm）")
    ap.add_argument("--confirm", action="store_true", help="确认危险操作")
    ap.add_argument("--list", action="store_true", help="列出所有 user 与块数")
    args = ap.parse_args()

    db = args.db or str(Path.home() / ".laap" / "aml_memory.sqlite3")
    if not Path(db).exists():
        print(f"数据库不存在: {db}")
        return 0
    svc = AMLMemoryService(db)
    print(f"数据库: {db}")
    print(f"总览: {svc.stats()}")

    if args.list or (not args.user and not args.all):
        with svc._db() as c:
            rows = c.execute(
                "SELECT user_id, COUNT(*) FROM chunks GROUP BY user_id "
                "ORDER BY 2 DESC").fetchall()
        if rows:
            print("\nuser_id / chunks:")
            for uid, n in rows:
                print(f"  {uid}  {n}")
        else:
            print("\n(空)")
        return 0

    if args.all:
        if not args.confirm:
            print("拒绝：--all 需要同时传 --confirm")
            return 2
        with svc._db() as c:
            n = c.execute("DELETE FROM chunks").rowcount
        print(f"已删除全部 {n} 条记忆块")
        return 0

    if args.user:
        n = svc.purge_user(args.user)
        print(f"已删除 user={args.user} 的 {n} 条记忆块")
        print(f"剩余: {svc.stats()}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

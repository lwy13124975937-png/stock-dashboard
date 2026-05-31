# -*- coding: utf-8 -*-
"""更新后提醒检查：先提醒数据更新失败，后续可扩展高仓位降温/回撤提醒。"""
import os
for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notify import notify, ensure_config
from project_paths import DB


def latest_update_issues():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            """
            SELECT update_time, task_name, status, retry_count, error_msg
            FROM update_log
            WHERE update_time LIKE ?
            ORDER BY id DESC
            """,
            (today + "%",),
        ).fetchall()
    finally:
        conn.close()
    failed = [r for r in rows if r[2] != "成功"]
    return rows, failed


def main():
    ensure_config()
    rows, failed = latest_update_issues()
    if not rows:
        title = "A股管理台：今日暂无更新日志"
        content = "今天没有发现 update_data.py 更新日志，请检查定时任务是否运行。"
    elif failed:
        title = "A股管理台：部分数据更新失败"
        lines = ["以下接口更新失败或无数据："]
        for t, name, status, retry_count, err in failed:
            lines.append(f"- {name}：{status}，重试 {retry_count} 次，原因：{err or '无'}")
        content = "\n".join(lines)
    else:
        print("今日更新日志正常，无需提醒。")
        return

    for ok, msg in notify(title, content):
        print("[提醒]", "成功" if ok else "未发送", msg)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""数据仓库更新器：每个交易日收盘后运行一次，积累历史并写入更新日志。"""
import os, time
for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"

import sqlite3, akshare as ak, pandas as pd
from datetime import datetime
from project_paths import DB

TODAY = datetime.now().strftime("%Y%m%d")
CODES = ["688981", "002371"]


def ensure_log_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS update_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            update_time TEXT,
            task_name TEXT,
            status TEXT,
            data_date TEXT,
            retry_count INTEGER,
            error_msg TEXT
        )
        """
    )
    conn.commit()


def log_update(conn, task_name, status, data_date="", retry_count=0, error_msg=""):
    conn.execute(
        """
        INSERT INTO update_log
        (update_time, task_name, status, data_date, retry_count, error_msg)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            task_name,
            status,
            data_date,
            int(retry_count or 0),
            str(error_msg or "")[:500],
        ),
    )
    conn.commit()


def fetch(fn, retries=4, wait=2):
    last = ""
    for i in range(retries):
        try:
            return fn(), i, ""
        except Exception as e:
            last = repr(e)
            if i < retries - 1:
                time.sleep(wait)
    return None, retries, last


def infer_data_date(df):
    if df is None or len(df) == 0:
        return ""
    for col in ("snapshot_date", "日期", "净值日期", "数据日期", "时间"):
        if col in df.columns:
            vals = df[col].dropna()
            if len(vals):
                return str(vals.iloc[-1])[:20]
    return TODAY


def save(conn, table, df, retry_count=0, error_msg=""):
    if df is None or len(df) == 0:
        msg = error_msg or "无数据返回（周末/节假日或接口暂无数据时可能正常）"
        print(f"   [跳过] {table}：{msg[:80]}")
        log_update(conn, table, "失败", "", retry_count, msg)
        return False
    df = df.copy()
    df["snapshot_date"] = TODAY
    try:
        conn.execute(f"DELETE FROM {table} WHERE snapshot_date=?", (TODAY,))
        conn.commit()
    except Exception:
        pass
    df.to_sql(table, conn, if_exists="append", index=False)
    print(f"   [已存] {table}：{len(df)} 行")
    log_update(conn, table, "成功", infer_data_date(df), retry_count, "")
    return True


def em(code):
    return ("SH" if code.startswith("6") else "SZ") + code


def main():
    conn = sqlite3.connect(DB)
    ensure_log_table(conn)
    print(f"=== 更新数据 {TODAY} -> 本地库 ===")

    # 板块情绪（同花顺，趋势就靠它攒历史）
    df, retry_count, err = fetch(lambda: ak.stock_board_industry_summary_ths())
    save(conn, "board_heat", df, retry_count, err)

    # 市场情绪快照（东财，能通就存；失败会写日志，不影响其他任务）
    fr = []
    hot_retry = 0
    hot_err = ""
    for c in CODES:
        d, rc, err = fetch(lambda c=c: ak.stock_hot_rank_latest_em(symbol=em(c)))
        hot_retry += rc
        if err:
            hot_err = err
        if d is not None and len(d):
            d = d.copy()
            d["代码"] = c
            fr.append(d)
    save(conn, "hot_rank", pd.concat(fr, ignore_index=True) if fr else None, hot_retry, hot_err)

    df, retry_count, err = fetch(lambda: ak.stock_hsgt_fund_flow_summary_em())
    save(conn, "north_flow", df, retry_count, err)

    df, retry_count, err = fetch(lambda: ak.stock_zt_pool_em(date=TODAY))
    save(conn, "zt_pool", df, retry_count, err)

    conn.close()
    print("=== 完成。每个交易日跑一次，历史和日志会自动变厚 ===")


if __name__ == "__main__":
    main()

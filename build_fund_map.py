# -*- coding: utf-8 -*-
"""基金透视：抓基金前十大重仓，按同花顺行业反向索引映射到主导板块。

一个季度跑一次即可：
    python build_fund_map.py

如果想强制重建同花顺行业成分股缓存：
    python build_fund_map.py --refresh-index
"""
import os
for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"

import re
import sys
import time
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from io import StringIO

import akshare as ak
import pandas as pd
import requests
from bs4 import BeautifulSoup
from my_holdings import HOLDINGS
from project_paths import DB

try:
    from akshare.stock_feature import stock_board_industry_ths as ths_mod
except Exception:
    ths_mod = None

# 只对这些类型的基金做透视（场外+场内基金）
FUND_TYPES = {"otc", "lof"}
INDEX_TABLE = "ths_stock_board_index"
A_SHARE_TABLE = "a_share_code_name"
INDEX_CACHE_DAYS = 30
MIN_A_SHARE_RATIO = 0.5

BOARD_ALIASES = {
    "航天装备Ⅱ": "军工装备",
    "军工电子Ⅱ": "军工电子",
    "IT服务Ⅱ": "IT服务",
    "油气开采Ⅱ": "油气开采及服务",
    "城商行Ⅱ": "银行",
    "股份制银行Ⅱ": "银行",
    "国有大型银行Ⅱ": "银行",
    "农商行Ⅱ": "银行",
    "塑料": "塑料制品",
    "综合Ⅱ": "综合",
    "旅游及景区": "旅游及酒店",
    "玻璃玻纤": "非金属材料",
}


def retry(fn, n=4, wait=2, label=""):
    """网络请求统一重试；失败返回 None，避免一次断网中断整批任务。"""
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < n - 1:
                print(f"   [重试] {label or '请求'} 第 {i+1}/{n} 次失败：{repr(e)[:80]}")
                time.sleep(wait)
    print(f"   [失败] {label or '请求'}：{repr(last)[:120]}")
    return None


def clean_code(x, allow_short=False):
    """把股票代码统一成 6 位；基金里的港股 00700 不误补成 A 股代码。"""
    s = str(x).strip().upper()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if not s.isdigit():
        return ""
    if len(s) == 6:
        return s
    if allow_short and 1 <= len(s) < 6:
        return s.zfill(6)
    return ""


def is_a_share_code(code):
    return len(code) == 6 and code[0] in "03689"


def normalize_board_name(board):
    board = str(board or "").strip()
    if not board:
        return None
    return BOARD_ALIASES.get(board, board.replace("Ⅱ", ""))


def normalize_stock_name(name):
    s = str(name or "").upper().strip()
    for old in (" ", "\u3000", "*", "-", "Ａ"):
        s = s.replace(old, "")
    s = s.replace("ST", "")
    for prefix in ("DR", "XD", "XR"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def cache_table_is_fresh(conn, table):
    try:
        row = conn.execute(f"SELECT COUNT(*), MAX(updated_at) FROM {table}").fetchone()
    except Exception:
        return False
    if not row or not row[0]:
        return False
    try:
        updated = datetime.fromisoformat(row[1])
        return updated >= datetime.now() - timedelta(days=INDEX_CACHE_DAYS)
    except Exception:
        return False


def ths_headers(referer="https://q.10jqka.com.cn/"):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Referer": referer,
    }
    if ths_mod is not None:
        try:
            js_code = ths_mod.py_mini_racer.MiniRacer()
            js_code.eval(ths_mod._get_file_content_ths("ths.js"))
            headers["Cookie"] = f"v={js_code.call('v')}"
        except Exception:
            pass
    return headers


def get_ths_board_list():
    """取同花顺行业名称和行业代码。"""
    df = retry(lambda: ak.stock_board_industry_name_ths(), label="同花顺行业列表")
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["name", "code"])
    return df[["name", "code"]].dropna().drop_duplicates().reset_index(drop=True)


def page_count(html):
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("span", attrs={"class": "page_info"})
    if not node or "/" not in node.text:
        return 1
    try:
        return int(node.text.split("/")[-1])
    except Exception:
        return 1


def read_stock_table(html):
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    df = tables[0].copy()
    if "代码" not in df.columns or "名称" not in df.columns:
        return pd.DataFrame()
    df["stock_code"] = df["代码"].map(lambda x: clean_code(x, allow_short=True))
    df["stock_name"] = df["名称"].astype(str).str.strip()
    df = df[df["stock_code"].str.len() == 6]
    return df[["stock_code", "stock_name"]].drop_duplicates()


def request_ths_html(url, headers):
    r = requests.get(url, headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    if "Nginx forbidden" in r.text or "No tables found" in r.text:
        raise RuntimeError("同花顺临时拒绝或无表格")
    return r.text


def request_stock_table(url, headers):
    html = request_ths_html(url, headers)
    df = read_stock_table(html)
    if len(df) == 0:
        raise RuntimeError("未解析到成分股表")
    return html, df


def fetch_board_constituents(board_name, board_code, wait=0.35):
    """用同花顺行业详情页抓成分股，不调用东财接口。"""
    base = f"https://q.10jqka.com.cn/thshy/detail/code/{board_code}/"
    headers = ths_headers(base)

    first = retry(
        lambda: request_stock_table(base, headers),
        label=f"{board_name} 第1页",
    )
    if not first:
        return pd.DataFrame(columns=["stock_code", "stock_name", "board_name", "board_code"])

    html, first_df = first
    total_pages = page_count(html)
    pieces = [first_df]

    # 同花顺行业详情页第 6 页以后容易跳登录；只抓免费可见页。
    # 大行业的个别遗漏再由 F10 个股行业兜底，避免全量建库时被登录墙拖慢。
    max_free_pages = min(total_pages, 5)
    strategies = [("code", "asc")]
    for field, order in strategies:
        for page in range(1, max_free_pages + 1):
            url = f"{base}field/{field}/order/{order}/page/{page}/ajax/1/"
            page_data = retry(
                lambda u=url: request_stock_table(u, headers),
                label=f"{board_name} {order} 第{page}页",
            )
            if page_data:
                _, page_df = page_data
                pieces.append(page_df)
            time.sleep(wait)

    out = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    if len(out) == 0:
        return pd.DataFrame(columns=["stock_code", "stock_name", "board_name", "board_code"])
    out = out.drop_duplicates("stock_code")
    out["board_name"] = board_name
    out["board_code"] = str(board_code)
    return out[["stock_code", "stock_name", "board_name", "board_code"]]


def cache_is_fresh(conn):
    return cache_table_is_fresh(conn, INDEX_TABLE)


def load_a_share_names(conn):
    if not cache_table_is_fresh(conn, A_SHARE_TABLE):
        df = retry(lambda: ak.stock_info_a_code_name(), label="A股代码名称表")
        if df is not None and len(df):
            tmp = f"{A_SHARE_TABLE}_new"
            conn.execute(f"DROP TABLE IF EXISTS {tmp}")
            conn.execute(
                f"""
                CREATE TABLE {tmp} (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    norm_name TEXT,
                    updated_at TEXT
                )
                """
            )
            updated_at = datetime.now().isoformat(timespec="seconds")
            out = df[["code", "name"]].copy()
            out["code"] = out["code"].astype(str).str.zfill(6)
            out["norm_name"] = out["name"].map(normalize_stock_name)
            out["updated_at"] = updated_at
            out.to_sql(tmp, conn, if_exists="append", index=False)
            conn.execute(f"DROP TABLE IF EXISTS {A_SHARE_TABLE}")
            conn.execute(f"ALTER TABLE {tmp} RENAME TO {A_SHARE_TABLE}")
            conn.commit()

    try:
        rows = conn.execute(f"SELECT code, norm_name FROM {A_SHARE_TABLE}").fetchall()
    except Exception:
        return {}
    return {code: name for code, name in rows}


def is_name_match_a_share(code, stock_name, a_share_names):
    expected = a_share_names.get(code)
    if not expected:
        return False
    actual = normalize_stock_name(stock_name)
    return actual == expected or actual in expected or expected in actual


def rebuild_board_index(conn):
    boards = get_ths_board_list()
    if len(boards) == 0:
        raise RuntimeError("同花顺行业列表拉取失败，无法建立成分股索引")

    tmp = f"{INDEX_TABLE}_new"
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.execute(
        f"""
        CREATE TABLE {tmp} (
            stock_code TEXT,
            stock_name TEXT,
            board_name TEXT,
            board_code TEXT,
            updated_at TEXT,
            PRIMARY KEY (stock_code, board_name)
        )
        """
    )
    conn.commit()

    updated_at = datetime.now().isoformat(timespec="seconds")
    total = 0
    print(f"=== 正在重建同花顺行业成分股缓存：{len(boards)} 个行业 ===")
    for i, row in boards.iterrows():
        board_name = str(row["name"]).strip()
        board_code = str(row["code"]).strip()
        print(f"[{i+1:02d}/{len(boards)}] {board_name}({board_code})")
        df = fetch_board_constituents(board_name, board_code)
        if len(df):
            df["updated_at"] = updated_at
            df.to_sql(tmp, conn, if_exists="append", index=False)
            conn.commit()
            total += len(df)
            print(f"   -> {len(df)} 只")
        else:
            print("   -> 0 只（已跳过）")
        time.sleep(0.4)

    if total == 0:
        raise RuntimeError("同花顺成分股缓存为空，保留旧缓存不覆盖")

    conn.execute(f"DROP TABLE IF EXISTS {INDEX_TABLE}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {INDEX_TABLE}")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{INDEX_TABLE}_code ON {INDEX_TABLE}(stock_code)")
    conn.commit()
    print(f"=== 同花顺行业成分股缓存完成：{total} 条 ===")


def load_board_index(conn, force_refresh=False):
    if force_refresh or not cache_is_fresh(conn):
        rebuild_board_index(conn)

    rows = conn.execute(
        f"SELECT stock_code, board_name FROM {INDEX_TABLE} ORDER BY stock_code, board_name"
    ).fetchall()
    board_index = defaultdict(list)
    for code, board in rows:
        if board not in board_index[code]:
            board_index[code].append(board)
    return board_index


def stock_board(code, board_index):
    """查一只 A 股所属同花顺行业；找不到返回 None。"""
    boards = board_index.get(clean_code(code), [])
    return normalize_board_name(boards[0]) if boards else None


def stock_board_from_ths_f10(code):
    """兜底：从同花顺 F10 个股页读取“所属申万行业”，仍然不调用东财。"""
    code = clean_code(code)
    if not is_a_share_code(code):
        return None

    def fetch():
        url = f"https://basic.10jqka.com.cn/{code}/"
        r = requests.get(url, headers=ths_headers(url), timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        r.encoding = "gb2312"
        soup = BeautifulSoup(r.text, "lxml")
        label = soup.find(string=lambda t: t and "所属申万行业" in t)
        if not label:
            return None
        node = label.find_parent("span")
        value = node.find_next("span", attrs={"class": "tip f14"}) if node else None
        if not value:
            return None
        return normalize_board_name(value.get_text(strip=True))

    return retry(fetch, n=3, wait=1, label=f"{code} 同花顺F10行业")


def save_board_index_row(conn, code, stock_name, board):
    if conn is None or not board:
        return
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {INDEX_TABLE}
        (stock_code, stock_name, board_name, board_code, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (code, stock_name, board, "", datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def resolve_stock_board(code, stock_name, board_index, a_share_names, conn=None):
    code = clean_code(code)
    if not is_a_share_code(code):
        return None
    if not is_name_match_a_share(code, stock_name, a_share_names):
        return None
    board = stock_board(code, board_index)
    if board:
        return board
    board = stock_board_from_ths_f10(code)
    if board:
        board_index[code].append(board)
        save_board_index_row(conn, code, stock_name, board)
    return board


def latest_fund_holdings(code):
    """取基金最新季度前十大重仓。"""
    df = retry(lambda: ak.fund_portfolio_hold_em(symbol=code, date="2026"), label=f"{code} 2026持仓")
    if df is None or len(df) == 0:
        df = retry(lambda: ak.fund_portfolio_hold_em(symbol=code, date="2025"), label=f"{code} 2025持仓")
    if df is None or len(df) == 0:
        return None

    if "季度" in df.columns:
        latest = sorted(df["季度"].dropna().unique())[-1]
        df = df[df["季度"] == latest]

    df = df.copy()
    df["占净值比例"] = pd.to_numeric(df["占净值比例"], errors="coerce")
    df = df.dropna(subset=["占净值比例"]).head(10)
    return df


def fund_main_board(code, board_index, a_share_names, conn=None):
    """按基金前十大重仓的占净值比例加权，找出主导同花顺行业。"""
    df = latest_fund_holdings(code)
    if df is None or len(df) == 0:
        return None, None

    weight = defaultdict(float)
    names = []
    total_weight = 0.0
    matched_weight = 0.0

    for _, r in df.iterrows():
        scode = clean_code(r["股票代码"])
        sname = str(r["股票名称"]).strip()
        w = float(r["占净值比例"])
        total_weight += w
        board = resolve_stock_board(scode, sname, board_index, a_share_names, conn)
        if board:
            matched_weight += w
            weight[board] += w
            names.append(f"{sname}({board}, {w:.2f}%)")
        else:
            names.append(f"{sname}(境外/非A股或未收录, {w:.2f}%)")

    # QDII/海外基金可能混有少量 A 股或中概映射，A 股权重不足时不强行贴 A 股行业。
    matched_ratio = matched_weight / total_weight if total_weight else 0
    if not weight or matched_ratio < MIN_A_SHARE_RATIO:
        return None, names

    main_board = max(weight, key=weight.get)
    detail = (
        f"A股匹配权重 {matched_weight:.2f}% / 前十大合计 {total_weight:.2f}%；"
        + "、".join(names)
    )
    return main_board, [detail]


def main():
    force_refresh = "--refresh-index" in sys.argv
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fund_board_map "
        "(code TEXT PRIMARY KEY, main_board TEXT, detail TEXT)"
    )
    conn.commit()

    board_index = load_board_index(conn, force_refresh=force_refresh)
    a_share_names = load_a_share_names(conn)

    seen = set()
    for h in HOLDINGS:
        if h["type"] not in FUND_TYPES:
            continue
        code = h["code"]
        if code in seen:
            continue
        seen.add(code)

        print(f"\n透视 {h['name']}（{code}）...")
        mb, names = fund_main_board(code, board_index, a_share_names, conn)
        detail = "、".join(names) if names else ""
        if mb:
            print(f"   -> 主导板块：{mb}")
            print(f"   详情：{detail}")
            conn.execute(
                "INSERT OR REPLACE INTO fund_board_map VALUES (?,?,?)",
                (code, mb, detail),
            )
        else:
            print("   -> 无A股主导板块（QDII/海外/商品基金，或A股权重不足）")
            conn.execute(
                "INSERT OR REPLACE INTO fund_board_map VALUES (?,?,?)",
                (code, "无（境外/非A股）", detail),
            )
        conn.commit()

    conn.close()
    print("\n=== 完成。结果已存入本地库 fund_board_map 表 ===")


if __name__ == "__main__":
    main()

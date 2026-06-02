# -*- coding: utf-8 -*-
"""每日资产和板块快照。

用于本地手动运行或 GitHub Actions 定时运行。输出文件在 history/ 目录，可提交到 Git。
"""
import os
for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"

import json
import re
import time
from datetime import datetime, timedelta, timezone

import akshare as ak
import pandas as pd
import requests

from my_holdings import HOLDINGS
from project_paths import BOARD_HEAT_HISTORY_FILE, HISTORY_DIR, SNAPSHOTS_FILE


ACCOUNTS = ["银河证券", "东方财富", "支付宝"]
ACCOUNT_KEYS = {
    "银河证券": "galaxy",
    "东方财富": "eastmoney",
    "支付宝": "alipay",
}


def china_today():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def sina_stock(code):
    return ("sh" if str(code).startswith("6") else "sz") + str(code)


def sina_fund(code):
    return ("sh" if str(code).startswith(("5", "6")) else "sz") + str(code)


def holding_snapshot_id(account, htype, code):
    account_key = ACCOUNT_KEYS.get(str(account), re.sub(r"\W+", "_", str(account)).strip("_") or "account")
    type_key = re.sub(r"\W+", "_", str(htype)).strip("_") or "asset"
    code_key = re.sub(r"\W+", "_", str(code)).strip("_") or "code"
    return f"{account_key}_{type_key}_{code_key}"


def retry(fn, n=4, wait=2):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < n - 1:
                time.sleep(wait)
    raise RuntimeError(repr(last))


def latest_close(code, is_fund):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    if is_fund:
        df = retry(lambda: ak.fund_etf_hist_sina(symbol=sina_fund(code)))
    else:
        df = retry(lambda: ak.stock_zh_a_daily(symbol=sina_stock(code), start_date=start, end_date=end, adjust="qfq"))
    if df is None or len(df) == 0:
        raise RuntimeError(f"无行情数据：{code}")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").dropna(subset=["close"])
    row = df.iloc[-1]
    return float(row["close"]), str(row["date"].date())


def eastmoney_otc_latest_nav(code):
    """天天基金最新已公布净值。只取 dwjz，不使用盘中估值 gsz。"""
    url = f"https://fundgz.1234567.com.cn/js/{str(code)}.js"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://fund.eastmoney.com/",
    }
    def fetch():
        resp = requests.get(url, headers=headers, params={"rt": int(time.time() * 1000)}, timeout=10)
        resp.raise_for_status()
        m = re.search(r"jsonpgz\((.*)\);?", resp.text.strip())
        if not m:
            raise RuntimeError("天天基金返回内容不是 JSONP")
        data = json.loads(m.group(1))
        nav = pd.to_numeric(data.get("dwjz"), errors="coerce")
        nav_date = str(data.get("jzrq") or "").strip()
        if pd.isna(nav) or not nav_date:
            raise RuntimeError("天天基金缺少 dwjz 或 jzrq")
        return float(nav), nav_date
    return retry(fetch, n=3, wait=1)


def akshare_otc_latest_nav(code):
    df = retry(lambda: ak.fund_open_fund_info_em(symbol=str(code), indicator="单位净值走势"))
    if df is None or len(df) == 0:
        raise RuntimeError(f"场外基金净值无数据：{code}")
    date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
    nav_col = "单位净值" if "单位净值" in df.columns else df.columns[1]
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
    df = df.dropna(subset=[date_col, nav_col]).sort_values(date_col)
    if len(df) == 0:
        raise RuntimeError(f"场外基金净值为空：{code}")
    row = df.iloc[-1]
    return float(row[nav_col]), str(row[date_col].date())


def quote_date_key(date_text):
    try:
        dt = pd.to_datetime(date_text, errors="coerce")
        return pd.Timestamp.min if pd.isna(dt) else dt
    except Exception:
        return pd.Timestamp.min


def latest_otc_nav(code):
    candidates = []
    first_error = None
    try:
        nav, nav_date = eastmoney_otc_latest_nav(code)
        candidates.append((nav, nav_date))
    except Exception as e:
        first_error = e
    try:
        nav, nav_date = akshare_otc_latest_nav(code)
        candidates.append((nav, nav_date))
    except Exception as e:
        if not candidates:
            raise first_error or e
    if not candidates:
        raise RuntimeError(f"场外基金净值无数据：{code}")
    nav, nav_date = sorted(candidates, key=lambda x: quote_date_key(x[1]))[-1]
    return nav, nav_date


def latest_hs300():
    df = retry(lambda: ak.stock_zh_index_daily(symbol="sh000300"))
    if df is None or len(df) == 0:
        raise RuntimeError("沪深300指数无数据")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").dropna(subset=["close"])
    row = df.iloc[-1]
    return float(row["close"]), str(row["date"].date())


def account_totals():
    totals = {acc: {"mv": 0.0, "cost": 0.0} for acc in ACCOUNTS}
    holding_totals = {}
    latest_dates = []
    for h in HOLDINGS:
        acc = h.get("account", "")
        totals.setdefault(acc, {"mv": 0.0, "cost": 0.0})
        if h.get("type") == "otc":
            shares = float(h.get("shares", 0) or 0)
            cost_price = float(h.get("cost", 0) or 0)
            if shares > 0 and cost_price > 0:
                try:
                    nav, data_date = latest_otc_nav(h["code"])
                    latest_dates.append(data_date)
                    mv = nav * shares
                    cost = cost_price * shares
                except Exception:
                    mv = float(h.get("market_value", 0) or 0)
                    profit = float(h.get("profit", 0) or 0)
                    cost = mv - profit
            else:
                mv = float(h.get("market_value", 0) or 0)
                profit = float(h.get("profit", 0) or 0)
                cost = mv - profit
        else:
            close, data_date = latest_close(h["code"], h.get("type") == "lof")
            latest_dates.append(data_date)
            shares = float(h.get("shares", 0) or 0)
            cost_price = float(h.get("cost", 0) or 0)
            mv = close * shares
            cost = cost_price * shares
        totals[acc]["mv"] += mv
        totals[acc]["cost"] += cost
        hid = holding_snapshot_id(acc, h.get("type", ""), h.get("code", ""))
        item = holding_totals.setdefault(hid, {
            "account": acc,
            "type": h.get("type", ""),
            "code": h.get("code", ""),
            "name": h.get("name", ""),
            "mv": 0.0,
            "cost": 0.0,
        })
        item["mv"] += mv
        item["cost"] += cost
    return totals, max(latest_dates) if latest_dates else "", holding_totals


def rate(mv, cost):
    return (mv - cost) / cost * 100 if cost else 0.0


def build_snapshot_row():
    totals, quote_date, holding_totals = account_totals()
    hs300_close, hs300_date = latest_hs300()
    snapshot_date = hs300_date or quote_date or china_today()
    total_mv = sum(v["mv"] for v in totals.values())
    total_cost = sum(v["cost"] for v in totals.values())
    row = {
        "date": snapshot_date,
        "quote_date": quote_date,
        "total_mv": round(total_mv, 2),
        "total_cost": round(total_cost, 2),
        "total_profit": round(total_mv - total_cost, 2),
        "total_return_pct": round(rate(total_mv, total_cost), 4),
        "hs300_close": round(hs300_close, 4),
        "hs300_date": hs300_date,
    }
    for acc in ACCOUNTS:
        data = totals.get(acc, {"mv": 0.0, "cost": 0.0})
        prefix = {
            "银河证券": "galaxy",
            "东方财富": "eastmoney",
            "支付宝": "alipay",
        }[acc]
        mv = data["mv"]
        cost = data["cost"]
        row[f"{prefix}_mv"] = round(mv, 2)
        row[f"{prefix}_cost"] = round(cost, 2)
        row[f"{prefix}_profit"] = round(mv - cost, 2)
        row[f"{prefix}_return_pct"] = round(rate(mv, cost), 4)
    for hid, data in holding_totals.items():
        mv = data["mv"]
        cost = data["cost"]
        row[f"holding_{hid}_mv"] = round(mv, 2)
        row[f"holding_{hid}_cost"] = round(cost, 2)
        row[f"holding_{hid}_profit"] = round(mv - cost, 2)
        row[f"holding_{hid}_return_pct"] = round(rate(mv, cost), 4)
    return row


def upsert_csv(path, row, key="date"):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame([row])
    if path.exists():
        old = pd.read_csv(path, dtype={key: str})
        old = old[old[key].astype(str) != str(row[key])]
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new
    out = out.sort_values(key)
    out.to_csv(path, index=False, encoding="utf-8-sig")


def update_board_heat_history(snapshot_date):
    df = retry(lambda: ak.stock_board_industry_summary_ths())
    if df is None or len(df) == 0:
        raise RuntimeError("同花顺板块情绪无数据")
    df = df.copy()
    df["snapshot_date"] = snapshot_date.replace("-", "")
    BOARD_HEAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if BOARD_HEAT_HISTORY_FILE.exists():
        old = pd.read_csv(BOARD_HEAT_HISTORY_FILE, dtype={"snapshot_date": str})
        old = old[old["snapshot_date"].astype(str) != df["snapshot_date"].iloc[0]]
        out = pd.concat([old, df], ignore_index=True)
    else:
        out = df
    out = out.sort_values(["snapshot_date", "板块"])
    out.to_csv(BOARD_HEAT_HISTORY_FILE, index=False, encoding="utf-8-sig")
    return len(df)


def main():
    if not HOLDINGS:
        raise RuntimeError("没有 HOLDINGS_DATA_JSON 或 holdings_data.json，无法生成个人资产快照。请先配置加密 Secret，或在高级功能里录入持仓。")
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    row = build_snapshot_row()
    upsert_csv(SNAPSHOTS_FILE, row)
    board_rows = update_board_heat_history(row["date"])
    print(f"已写入 {SNAPSHOTS_FILE}：{row['date']} 总资产 {row['total_mv']}")
    print(f"已写入 {BOARD_HEAT_HISTORY_FILE}：{board_rows} 条板块快照")


if __name__ == "__main__":
    main()

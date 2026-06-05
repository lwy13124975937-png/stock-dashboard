# -*- coding: utf-8 -*-
"""统一净值/行情取数。

get_nav(code, holding_type) 是项目内唯一的最新价格/净值入口：
- A股股票：新浪日线；
- 场内 ETF/LOF：新浪实时行情，失败回退新浪日线；
- 场外基金：先用前十大重仓自动判断 A股主题 / 境外QDII / 未知，再选择天天基金或 AkShare。
"""
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

for v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import akshare as ak
import pandas as pd
import requests

from project_paths import NAV_CACHE_FILE


@dataclass
class NavResult:
    code: str
    classify: str
    source: str
    nav: float | None
    date: str
    kind: str
    change_pct: float | None = None
    reason: str = ""
    cache: bool = False
    prev_nav: float | None = None
    prev_date: str = ""


def china_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)


def china_today_string():
    return china_now().strftime("%Y-%m-%d")


def market_cache_key():
    now = china_now()
    phase = "after_close" if (now.hour, now.minute) >= (15, 5) else "intraday"
    return f"{now:%Y-%m-%d}:{phase}"


def clean_code(code):
    digits = re.sub(r"\D", "", str(code or ""))
    return digits.zfill(6) if 0 < len(digits) <= 6 else digits


def is_a_share_code(code):
    code = clean_code(code)
    return len(code) == 6 and code[0] in "03689"


def sina_stock_symbol(code):
    code = clean_code(code)
    return ("sh" if code.startswith("6") else "sz") + code


def sina_fund_symbol(code):
    code = clean_code(code)
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def to_float(value):
    n = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(n) else float(n)


def retry(fn, n=3, wait=1):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < n - 1:
                time.sleep(wait)
    raise RuntimeError(str(last))


def read_nav_cache():
    try:
        if Path(NAV_CACHE_FILE).exists():
            return json.loads(Path(NAV_CACHE_FILE).read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def write_nav_cache(result):
    if result.nav is None or result.cache:
        return
    try:
        data = read_nav_cache()
        data[result.code] = asdict(result) | {"saved_at": china_now().isoformat(timespec="seconds")}
        Path(NAV_CACHE_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def cached_result(code, classify, reason):
    item = read_nav_cache().get(clean_code(code))
    if not item:
        return NavResult(clean_code(code), classify, "全部接口失败", None, "", "失败", None, reason, False)
    return NavResult(
        code=clean_code(code),
        classify=classify,
        source="本地缓存",
        nav=to_float(item.get("nav")),
        date=str(item.get("date", "")),
        kind="缓存",
        change_pct=to_float(item.get("change_pct")),
        reason=f"接口全部失败，使用缓存值 截至{item.get('date', '未知日期')}；{reason}",
        cache=True,
        prev_nav=to_float(item.get("prev_nav")),
        prev_date=str(item.get("prev_date", "")),
    )


def sina_quote(symbol):
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

    def fetch():
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = "gbk"
        m = re.search(r'var hq_str_[a-z]{2}\d{6}="(.*)";', resp.text)
        if not m:
            raise RuntimeError("新浪实时行情返回为空")
        parts = m.group(1).split(",")
        if len(parts) < 4 or not parts[0]:
            raise RuntimeError("新浪实时行情字段不足")
        prev = to_float(parts[2])
        price = to_float(parts[3])
        if not price or price <= 0:
            price = to_float(parts[1])
        if not price or price <= 0:
            raise RuntimeError("新浪实时行情价格为空")
        date = parts[30] if len(parts) > 30 else china_today_string()
        change_pct = (price / prev - 1) * 100 if prev else None
        return price, date, prev, change_pct

    return retry(fetch, n=3, wait=1)


def stock_daily_latest(code):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")

    def fetch():
        df = ak.stock_zh_a_daily(symbol=sina_stock_symbol(code), start_date=start, end_date=end, adjust="qfq")
        if df is None or len(df) == 0:
            raise RuntimeError("新浪A股日线为空")
        df = df.rename(columns={c: str(c).lower() for c in df.columns})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date")
        row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else row
        prev = float(prev_row["close"])
        close = float(row["close"])
        return close, str(row["date"].date()), prev, str(prev_row["date"].date()), (close / prev - 1) * 100 if prev else None

    return retry(fetch, n=4, wait=2)


def exchange_fund_daily_latest(code):
    def fetch():
        df = ak.fund_etf_hist_sina(symbol=sina_fund_symbol(code))
        if df is None or len(df) == 0:
            raise RuntimeError("新浪场内基金日线为空")
        df = df.rename(columns={c: str(c).lower() for c in df.columns})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["date", "close"]).sort_values("date")
        row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else row
        prev = float(prev_row["close"])
        close = float(row["close"])
        return close, str(row["date"].date()), prev, str(prev_row["date"].date()), (close / prev - 1) * 100 if prev else None

    return retry(fetch, n=4, wait=2)


def fundgz_data(code):
    code = clean_code(code)
    url = f"https://fundgz.1234567.com.cn/js/{code}.js"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}

    def fetch():
        resp = requests.get(url, headers=headers, params={"rt": int(time.time() * 1000)}, timeout=10)
        resp.raise_for_status()
        m = re.search(r"jsonpgz\((.*)\);?", resp.text.strip())
        if not m:
            raise RuntimeError("天天基金返回内容不是JSONP")
        return json.loads(m.group(1))

    return retry(fetch, n=3, wait=1)


def eastmoney_lsjz_latest(code):
    code = clean_code(code)
    url = "https://api.fund.eastmoney.com/f10/lsjz"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://fund.eastmoney.com/{code}.html",
        "Accept": "application/json,text/plain,*/*",
    }

    def fetch():
        resp = requests.get(
            url,
            headers=headers,
            params={"fundCode": code, "pageIndex": 1, "pageSize": 5},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = ((data.get("Data") or {}).get("LSJZList") or [])
        if not rows:
            raise RuntimeError("东方财富历史净值列表为空")
        parsed = []
        for row in rows:
            date = str(row.get("FSRQ") or "").strip()
            nav = to_float(row.get("DWJZ"))
            if date and nav:
                parsed.append((pd.to_datetime(date), float(nav), row))
        if not parsed:
            raise RuntimeError("东方财富历史净值缺少日期或单位净值")
        parsed = sorted(parsed, key=lambda x: x[0], reverse=True)
        latest_dt, nav, latest_row = parsed[0]
        prev_dt, prev_nav, _ = parsed[1] if len(parsed) > 1 else parsed[0]
        change_pct = to_float(latest_row.get("JZZZL"))
        if change_pct is None and prev_nav:
            change_pct = (nav / prev_nav - 1) * 100
        return nav, str(latest_dt.date()), float(prev_nav), str(prev_dt.date()), change_pct

    return retry(fetch, n=3, wait=1)


def open_fund_nav_frame(code):
    def fetch():
        df = ak.fund_open_fund_info_em(symbol=clean_code(code), indicator="单位净值走势")
        if df is None or len(df) == 0:
            raise RuntimeError("AkShare场外净值为空")
        return df

    return retry(fetch, n=4, wait=2)


def akshare_open_latest(code):
    df = open_fund_nav_frame(code).copy()
    date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
    nav_col = "单位净值" if "单位净值" in df.columns else df.columns[1]
    growth_col = "日增长率" if "日增长率" in df.columns else None
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
    if growth_col:
        df[growth_col] = pd.to_numeric(df[growth_col], errors="coerce")
    df = df.dropna(subset=[date_col, nav_col]).sort_values(date_col)
    if len(df) == 0:
        raise RuntimeError("AkShare净值解析后为空")
    row = df.iloc[-1]
    nav = float(row[nav_col])
    prev_row = df.iloc[-2] if len(df) > 1 else row
    prev_nav = float(prev_row[nav_col])
    prev_date = str(prev_row[date_col].date())
    if growth_col and pd.notna(row.get(growth_col)):
        change_pct = float(row[growth_col])
    else:
        change_pct = (nav / prev_nav - 1) * 100 if prev_nav else None
    return nav, str(row[date_col].date()), prev_nav, prev_date, change_pct


def akshare_open_previous_before(code, before_date):
    df = open_fund_nav_frame(code).copy()
    date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
    nav_col = "单位净值" if "单位净值" in df.columns else df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
    df = df.dropna(subset=[date_col, nav_col]).sort_values(date_col)
    target = pd.to_datetime(before_date, errors="coerce")
    if pd.isna(target):
        raise RuntimeError("目标日期无效，无法取上一净值")
    prev = df[df[date_col] < target]
    if len(prev) == 0:
        raise RuntimeError(f"{before_date} 之前没有历史净值")
    row = prev.iloc[-1]
    return float(row[nav_col]), str(row[date_col].date())


@lru_cache(maxsize=512)
def fund_portfolio_profile(code):
    """用前十大重仓自动判断场外基金是否以A股为主。"""
    code = clean_code(code)
    last = None
    df = None
    for year in ("2026", "2025"):
        try:
            df = ak.fund_portfolio_hold_em(symbol=code, date=year)
            if df is not None and len(df):
                break
        except Exception as e:
            last = e
            time.sleep(1)
    if df is None or len(df) == 0:
        return {
            "classify": "场外基金-类型未知",
            "a_weight": None,
            "total_weight": None,
            "a_ratio": None,
            "reason": f"前十大重仓暂未取得：{str(last)[:80] if last else '无数据'}",
        }
    if "季度" in df.columns:
        latest = sorted(df["季度"].dropna().unique())[-1]
        df = df[df["季度"] == latest]
    df = df.copy().head(10)
    df["占净值比例"] = pd.to_numeric(df["占净值比例"], errors="coerce")
    df = df.dropna(subset=["占净值比例"])
    total = float(df["占净值比例"].sum()) if len(df) else 0.0
    a_weight = 0.0
    for _, row in df.iterrows():
        scode = clean_code(row.get("股票代码"))
        if is_a_share_code(scode):
            a_weight += float(row.get("占净值比例", 0) or 0)
    ratio = a_weight / total if total else 0.0
    classify = "场外基金-A股主题" if total and ratio >= 0.5 else "场外基金-QDII/境外"
    return {
        "classify": classify,
        "a_weight": a_weight,
        "total_weight": total,
        "a_ratio": ratio,
        "reason": f"前十大A股权重 {a_weight:.2f}% / 合计 {total:.2f}%",
    }


def classify_fund(code, holding_type="", name=""):
    htype = str(holding_type or "").lower()
    if htype == "stock":
        return "A股股票", "持仓类型=stock"
    if htype == "lof":
        return "场内基金", "持仓类型=lof"
    if htype == "otc":
        profile = fund_portfolio_profile(code)
        if profile["classify"] != "场外基金-类型未知":
            return profile["classify"], profile["reason"]
        n = str(name or "")
        if re.search(r"QDII|全球|海外|港股|港美|美股|新兴市场|纳斯达克|标普|恒生", n, re.I):
            return "场外基金-QDII/境外", profile["reason"] + "；按基金名称识别为境外/QDII"
        return "场外基金-类型未知", profile["reason"]
    if clean_code(code).startswith(("15", "16", "50", "51", "56", "58")):
        return "场内基金", "按代码段识别为场内基金"
    return "基金类型未知", "未提供持仓类型"


def get_stock_nav(code):
    try:
        nav, date, prev_nav, prev_date, change_pct = stock_daily_latest(code)
        result = NavResult(clean_code(code), "A股股票", "新浪A股日线", nav, date, "真", change_pct, prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        return cached_result(code, "A股股票", f"新浪A股日线失败：{str(e)[:100]}")


def get_exchange_fund_nav(code):
    code = clean_code(code)
    errors = []
    try:
        nav, date, prev_nav, change_pct = sina_quote(sina_fund_symbol(code))
        prev_date = ""
        try:
            _, daily_date, _, daily_prev_date, _ = exchange_fund_daily_latest(code)
            prev_date = daily_date if daily_date < date else daily_prev_date
        except Exception:
            prev_date = "上一交易日"
        result = NavResult(code, "场内基金", "新浪实时行情", nav, date, "真", change_pct, prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"新浪实时失败：{str(e)[:80]}")
    try:
        nav, date, prev_nav, prev_date, change_pct = exchange_fund_daily_latest(code)
        result = NavResult(code, "场内基金", "新浪场内基金日线", nav, date, "真", change_pct, "实时失败后回退日线", prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"新浪日线失败：{str(e)[:80]}")
    return cached_result(code, "场内基金", "；".join(errors))


def get_otc_a_share_nav(code, name=""):
    code = clean_code(code)
    errors = []
    after_close = (china_now().hour, china_now().minute) >= (15, 5)
    if after_close:
        try:
            nav, date, prev_nav, prev_date, change_pct = eastmoney_lsjz_latest(code)
            reason = "东方财富历史净值接口，优先取最新已披露真净值"
            result = NavResult(code, "场外基金-A股主题", "东方财富历史净值lsjz", nav, date, "真", change_pct, reason, prev_nav=prev_nav, prev_date=prev_date)
            write_nav_cache(result)
            return result
        except Exception as e:
            errors.append(f"东方财富历史净值失败：{str(e)[:100]}")
    try:
        data = fundgz_data(code)
        true_nav = to_float(data.get("dwjz"))
        true_date = str(data.get("jzrq") or "").strip()
        est_nav = to_float(data.get("gsz"))
        est_pct = to_float(data.get("gszzl"))
        est_time = str(data.get("gztime") or "").strip()
        est_date = str(pd.to_datetime(est_time, errors="coerce").date()) if est_time else ""
        today = china_today_string()
        if after_close and true_nav and true_date == today:
            prev_nav, prev_date = akshare_open_previous_before(code, true_date)
            change_pct = (true_nav / prev_nav - 1) * 100 if prev_nav else None
            result = NavResult(code, "场外基金-A股主题", "天天基金fundgz-dwjz", true_nav, true_date, "真", change_pct, prev_nav=prev_nav, prev_date=prev_date)
            write_nav_cache(result)
            return result
        if (not after_close) and est_nav and est_date == today:
            prev_nav, prev_date = (true_nav, true_date) if true_nav and true_date else akshare_open_previous_before(code, est_date)
            change_pct = (est_nav / prev_nav - 1) * 100 if prev_nav else est_pct
            result = NavResult(code, "场外基金-A股主题", "天天基金fundgz-gsz", est_nav, est_date, "估", change_pct, prev_nav=prev_nav, prev_date=prev_date)
            write_nav_cache(result)
            return result
        if true_nav and true_date:
            prev_nav, prev_date = akshare_open_previous_before(code, true_date)
            change_pct = (true_nav / prev_nav - 1) * 100 if prev_nav else None
            result = NavResult(code, "场外基金-A股主题", "天天基金fundgz-dwjz", true_nav, true_date, "真", change_pct, "最新已披露真净值，日期按接口返回", prev_nav=prev_nav, prev_date=prev_date)
            write_nav_cache(result)
            return result
        raise RuntimeError("fundgz未给出可用gsz/dwjz")
    except Exception as e:
        errors.append(f"天天基金fundgz失败：{str(e)[:100]}")
    try:
        nav, date, prev_nav, prev_date, change_pct = eastmoney_lsjz_latest(code)
        result = NavResult(code, "场外基金-A股主题", "东方财富历史净值lsjz", nav, date, "真", change_pct, "fundgz失败后回退东方财富历史净值", prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"东方财富历史净值失败：{str(e)[:100]}")
    try:
        nav, date, prev_nav, prev_date, change_pct = akshare_open_latest(code)
        result = NavResult(code, "场外基金-A股主题", "AkShare单位净值走势", nav, date, "真", change_pct, "fundgz失败后回退AkShare", prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"AkShare净值失败：{str(e)[:100]}")
    return cached_result(code, "场外基金-A股主题", "；".join(errors))


def get_otc_qdii_nav(code, classify="场外基金-QDII/境外"):
    code = clean_code(code)
    errors = []
    try:
        nav, date, prev_nav, prev_date, change_pct = eastmoney_lsjz_latest(code)
        reason = "东方财富历史净值接口，QDII/境外按基金披露节奏滞后更新"
        if date != china_today_string():
            reason += f"；最新披露日为{date}，日期滞后属正常"
        result = NavResult(code, classify, "东方财富历史净值lsjz", nav, date, "真", change_pct, reason, prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"东方财富历史净值失败：{str(e)[:100]}")
    try:
        nav, date, prev_nav, prev_date, change_pct = akshare_open_latest(code)
        reason = "QDII/境外净值按基金披露节奏滞后更新"
        if date != china_today_string():
            reason += f"；最新披露日为{date}，日期滞后属正常"
        result = NavResult(code, classify, "AkShare单位净值走势", nav, date, "真", change_pct, reason, prev_nav=prev_nav, prev_date=prev_date)
        write_nav_cache(result)
        return result
    except Exception as e:
        errors.append(f"AkShare净值失败：{str(e)[:100]}")
    try:
        data = fundgz_data(code)
        true_nav = to_float(data.get("dwjz"))
        true_date = str(data.get("jzrq") or "").strip()
        if true_nav and true_date:
            prev_nav, prev_date = akshare_open_previous_before(code, true_date)
            change_pct = (true_nav / prev_nav - 1) * 100 if prev_nav else None
            reason = "AkShare失败后回退fundgz；QDII净值披露可能滞后"
            if true_date != china_today_string():
                reason += f"；最新披露日为{true_date}，日期滞后属正常"
            result = NavResult(code, classify, "天天基金fundgz-dwjz", true_nav, true_date, "真", change_pct, reason, prev_nav=prev_nav, prev_date=prev_date)
            write_nav_cache(result)
            return result
        raise RuntimeError("fundgz未给出dwjz")
    except Exception as e:
        errors.append(f"天天基金fundgz失败：{str(e)[:100]}")
    return cached_result(code, classify, "；".join(errors))


def get_nav(code, holding_type="", name="", cache_key=None):
    """统一获取最新价格/净值。cache_key 由页面传入交易日阶段，用于Streamlit外层缓存失效。"""
    _ = cache_key
    code = clean_code(code)
    classify, reason = classify_fund(code, holding_type, name)
    if classify == "A股股票":
        return get_stock_nav(code)
    if classify == "场内基金":
        return get_exchange_fund_nav(code)
    if classify == "场外基金-A股主题":
        result = get_otc_a_share_nav(code, name)
        result.reason = (result.reason + "；" if result.reason else "") + reason
        return result
    if classify in ("场外基金-QDII/境外", "场外基金-类型未知"):
        result = get_otc_qdii_nav(code, classify)
        result.classify = classify
        result.reason = (result.reason + "；" if result.reason else "") + reason
        return result
    return cached_result(code, classify, reason)


def calc_daily_return(result, shares):
    try:
        shares = float(shares)
    except Exception:
        shares = 0.0
    if shares <= 0:
        return None, None, "份额缺失"
    if result.nav is None:
        return None, None, "暂无数据"
    if result.cache:
        return None, None, "接口失败，用缓存值，不算当日收益"
    if result.prev_nav is None:
        return None, None, "昨值缺失"
    amount = (float(result.nav) - float(result.prev_nav)) * shares
    pct = (float(result.nav) / float(result.prev_nav) - 1) * 100 if result.prev_nav else None
    status = result.kind
    if result.classify.startswith("场外基金") and result.kind == "真" and result.date != china_today_string():
        status = "最新披露"
    return amount, pct, status


def current_holdings_daily_check():
    from my_holdings import HOLDINGS

    rows = []
    for h in HOLDINGS:
        result = get_nav(h.get("code", ""), h.get("type", ""), h.get("name", ""), cache_key=market_cache_key())
        shares = to_float(h.get("shares"))
        amount, pct, status = calc_daily_return(result, shares)
        rows.append({
            "代码": clean_code(h.get("code", "")),
            "名称": h.get("name", ""),
            "类型": h.get("type", ""),
            "份额": "缺失" if shares is None else f"{shares:.4f}",
            "昨净值": "—" if result.prev_nav is None else f"{result.prev_nav:.4f}",
            "昨日期": result.prev_date or "—",
            "今净值": "—" if result.nav is None else f"{result.nav:.4f}",
            "今日期": result.date or "—",
            "算出当日收益额": "—" if amount is None else f"{amount:+.2f}",
            "算出%": "—" if pct is None else f"{pct:+.2f}%",
            "数据源": result.source,
            "真/估/待披露": status,
            "类型判断": result.classify,
            "说明": result.reason,
        })
    return pd.DataFrame(rows)


def current_holdings_self_check():
    rows = []
    for _, row in current_holdings_daily_check().iterrows():
        rows.append({
            "代码": row["代码"],
            "名称": row["名称"],
            "持仓类型": row["类型"],
            "类型判断": row["类型判断"],
            "命中接口": row["数据源"],
            "净值/价格": row["今净值"],
            "披露日": row["今日期"],
            "估/真/缓存": row["真/估/待披露"],
            "说明": row["说明"],
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = current_holdings_daily_check()
    print(df.to_string(index=False))

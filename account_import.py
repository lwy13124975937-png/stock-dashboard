# -*- coding: utf-8 -*-
"""Three-account holdings import parser and preview builder."""
import json
import re

import pandas as pd

from alipay_json_import import infer_share_class, normalize_fund_name
from holding_import import clean_code
from nav_utils import get_nav, market_cache_key


ACCOUNT_IMPORT_PROMPTS = {
    "支付宝": '''你是基金持仓解析器，这是支付宝/养基宝"我的持有"列表。只输出JSON数组无解释无markdown。每只：name(含A/C) / share_class("A"/"C"/"") / amount金额 / profit持有收益(亏损负) / profit_rate收益率去% / account:"支付宝"。列表无份额无成本不要编造。
示例：[{"name":"永赢高端装备智选混合A","share_class":"A","amount":15529.24,"profit":-3071.03,"profit_rate":-16.51,"account":"支付宝"}]''',
    "银河证券": '''你是场内基金解析器，这是银河证券"我的场内资产"。只输出JSON数组无解释无markdown。每行(同代码多行各输出勿合并)：name / code(右侧6位直接抄) / shares持仓列 / cost("成本/现价"列【上方】=成本) / market_value(名称下方市值) / account:"银河证券"。成本是上面那个别取成现价。
示例：[{"name":"港美互联网LOF","code":"160644","shares":6168,"cost":1.567,"market_value":13458.58,"account":"银河证券"}]''',
    "东方财富": '''你是券商持仓解析器，这是东方财富"持仓"页(有股票和场内基金两段)。只输出JSON数组无解释无markdown。每行：name / shares持仓列 / price("现价/成本"列【上方】=现价) / cost(【下方】=成本) / market_value(名称下方市值) / account:"东方财富"。上现价下成本别取反，无代码不填。
示例：[{"name":"株冶集团","shares":200,"price":25.140,"cost":17.295,"market_value":5028.00,"account":"东方财富"}]''',
}


ACCOUNT_IMPORT_SAMPLES = {
    "支付宝": '''[
  {"name":"永赢高端装备智选混合A","share_class":"A","amount":15529.24,"profit":-3071.03,"profit_rate":-16.51,"account":"支付宝"},
  {"name":"易方达科鑫量化混合A","share_class":"A","amount":781.48,"profit":-18.49,"profit_rate":-2.31,"account":"支付宝"}
]''',
    "银河证券": '''[
  {"name":"港美互联网LOF","code":"160644","shares":6168,"cost":1.567,"market_value":13458.58,"account":"银河证券"},
  {"name":"港美互联网LOF","code":"160644","shares":2885,"cost":1.865,"market_value":6378.85,"account":"银河证券"},
  {"name":"国投白银LOF","code":"161226","shares":525,"cost":2.439,"market_value":1280.48,"account":"银河证券"},
  {"name":"机器人ETF","code":"501312","shares":100,"cost":1.234,"market_value":123.40,"account":"银河证券"}
]''',
    "东方财富": '''[
  {"name":"株冶集团","shares":200,"price":25.140,"cost":17.295,"market_value":5028.00,"account":"东方财富"},
  {"name":"白银基金","shares":587,"price":2.439,"cost":2.038,"market_value":1431.69,"account":"东方财富"}
]''',
}


EASTMONEY_SEED_CODES = {
    "株冶集团": "600961",
    "贵研铂业": "600459",
    "格力电器": "000651",
    "洛阳钼业": "603993",
    "赛轮轮胎": "601058",
    "白银基金": "161226",
    "国投白银": "161226",
    "国投白银LOF": "161226",
}


def _to_float(value, field):
    if value is None or value == "":
        raise ValueError(f"{field} 不能为空")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = text.replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    text = text.replace("元", "").replace("%", "").replace("+", "").replace("−", "-")
    return float(text)


def _norm(text):
    text = str(text or "").lower()
    text = re.sub(r"\([^)]*\)|（[^）]*）", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)
    return text


def holding_type_for_code(code, account):
    code = clean_code(code)
    if account == "支付宝":
        return "otc"
    if account == "银河证券":
        return "lof"
    if code.startswith(("1", "5")):
        return "lof"
    return "stock"


def parse_account_json(text, account):
    text = (text or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("holdings", data.get("items", []))
    if not isinstance(data, list):
        raise ValueError("JSON 必须是数组，例如 [{...}, {...}]")
    rows = []
    for idx, raw in enumerate(data, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {idx} 行不是 JSON 对象")
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"第 {idx} 行缺少 name")
        item = {"name": name, "account": str(raw.get("account") or account).strip()}
        if item["account"] != account:
            raise ValueError(f"第 {idx} 行 account 是 {item['account']}，与当前选择的 {account} 不一致")
        item["share_class"] = infer_share_class(name, raw.get("share_class", ""))
        if account == "支付宝":
            item["amount"] = _to_float(raw.get("amount"), "amount")
            item["profit"] = _to_float(raw.get("profit"), "profit")
            item["profit_rate"] = None if raw.get("profit_rate") in (None, "") else _to_float(raw.get("profit_rate"), "profit_rate")
            item["code"] = clean_code(raw.get("code", ""))
        elif account == "银河证券":
            item["code"] = clean_code(raw.get("code", ""))
            item["shares"] = _to_float(raw.get("shares"), "shares")
            item["unit_cost"] = _to_float(raw.get("unit_cost", raw.get("cost")), "cost")
            item["market_value"] = None if raw.get("market_value") in (None, "") else _to_float(raw.get("market_value"), "market_value")
        else:
            item["code"] = clean_code(raw.get("code", ""))
            item["shares"] = _to_float(raw.get("shares"), "shares")
            item["unit_cost"] = _to_float(raw.get("unit_cost", raw.get("cost")), "cost")
            item["price"] = None if raw.get("price") in (None, "") else _to_float(raw.get("price"), "price")
            item["market_value"] = None if raw.get("market_value") in (None, "") else _to_float(raw.get("market_value"), "market_value")
        rows.append(item)
    return rows


def account_code_options(records, account):
    seen = set()
    out = []
    for r in records:
        if r.get("account") != account:
            continue
        code = clean_code(r.get("code", ""))
        if code and code not in seen:
            out.append({"code": code, "name": r.get("name", ""), "share_class": str(r.get("share_class", "") or "")})
            seen.add(code)
    if account == "东方财富":
        for name, code in EASTMONEY_SEED_CODES.items():
            if code not in seen:
                out.append({"code": code, "name": name, "share_class": ""})
                seen.add(code)
    return out


def match_code(item, account, records):
    direct = clean_code(item.get("code", ""))
    if direct:
        return direct
    name = item.get("name", "")
    if account == "东方财富":
        norm_name = _norm(name)
        for seed_name, code in EASTMONEY_SEED_CODES.items():
            n = _norm(seed_name)
            if n and (n in norm_name or norm_name in n):
                return code
    options = account_code_options(records, account)
    item_name = normalize_fund_name(name) if account == "支付宝" else _norm(name)
    item_class = str(item.get("share_class", "") or "")
    scored = []
    for opt in options:
        rec_name = normalize_fund_name(opt["name"]) if account == "支付宝" else _norm(opt["name"])
        rec_class = str(opt.get("share_class", "") or "")
        if item_class and rec_class and item_class != rec_class:
            continue
        if rec_name and item_name and (rec_name in item_name or item_name in rec_name):
            score = min(len(rec_name), len(item_name))
            if item_class and rec_class == item_class:
                score += 100
            scored.append((score, opt["code"]))
    if not scored:
        return ""
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _merge_position_rows(rows):
    grouped = {}
    order = []
    for row in rows:
        key = (row["account"], row["type"], row["code"], row.get("share_class", ""))
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)
    merged = []
    notes = []
    for key in order:
        parts = grouped[key]
        if len(parts) == 1:
            merged.append(parts[0])
            continue
        total_shares = sum(float(r.get("shares", 0) or 0) for r in parts)
        total_cost = sum(float(r.get("shares", 0) or 0) * float(r.get("unit_cost", 0) or 0) for r in parts)
        first = parts[0]
        item = {
            "account": first["account"],
            "type": first["type"],
            "name": first["name"],
            "code": first["code"],
            "share_class": first.get("share_class", ""),
            "shares": round(total_shares, 4),
            "unit_cost": round(total_cost / total_shares, 6) if total_shares else 0,
        }
        merged.append(item)
        notes.append(f"{first['account']} {first['code']} 已合并 {len(parts)} 行：份额相加，单位成本按份额加权。")
    return merged, notes


def build_account_preview(items, account, records, selected_codes=None):
    selected_codes = selected_codes or {}
    proposed_raw = []
    preview = []
    for idx, item in enumerate(items):
        code = clean_code(selected_codes.get(idx) or match_code(item, account, records))
        row = {
            "账户": account,
            "名称": item["name"],
            "类别": item.get("share_class", ""),
            "代码": code or "待选择",
            "份额": "—",
            "单位成本": "—",
            "今NAV+日期": "—",
            "算出市值": "—",
            "持有收益": "—",
            "收益率": "—",
            "截图市值": "—",
            "状态": "可写入",
            "提示": "",
            "是否可写入": "是",
        }
        notes = []
        ok = True
        if not code:
            ok = False
            row["状态"] = "需手选代码"
            notes.append("未匹配到代码")

        htype = holding_type_for_code(code, account) if code else ("otc" if account == "支付宝" else "stock")
        nav_result = None
        nav = None
        nav_date = ""
        if code:
            try:
                nav_result = get_nav(code, htype, item["name"], cache_key=market_cache_key())
                nav = nav_result.nav
                nav_date = nav_result.date or ""
                if nav is not None:
                    row["今NAV+日期"] = f"{nav:.4f} / {nav_date} / {nav_result.kind or nav_result.source}"
                else:
                    row["今NAV+日期"] = f"— / {nav_result.reason or '接口失败'}"
            except Exception as e:
                row["今NAV+日期"] = f"— / {type(e).__name__}: {str(e)[:80]}"

        screenshot_mv = None
        calc_mv = None
        check_mv = None
        if account == "支付宝":
            screenshot_mv = float(item["amount"])
            if nav is None or nav <= 0:
                ok = False
                row["状态"] = "净值失败"
                notes.append("支付宝反推必须先取到最近披露净值")
                shares = 0
                unit_cost = 0
            else:
                shares = screenshot_mv / nav
                unit_cost = (screenshot_mv - float(item["profit"])) / shares
                calc_mv = shares * nav
        else:
            shares = float(item["shares"])
            unit_cost = float(item["unit_cost"])
            screenshot_mv = item.get("market_value")
            if nav is not None:
                calc_mv = shares * nav
            if item.get("price") not in (None, ""):
                check_mv = shares * float(item["price"])
                if calc_mv is None:
                    calc_mv = check_mv
                notes.append("校验使用截图现价")
            elif screenshot_mv not in (None, ""):
                check_mv = float(screenshot_mv)

        if ok:
            row["份额"] = f"{shares:.4f}"
            row["单位成本"] = f"{unit_cost:.4f}"
            if calc_mv is not None and pd.notna(calc_mv):
                row["算出市值"] = f"{calc_mv:,.2f}"
                profit = shares * ((nav if nav is not None else float(item.get("price", unit_cost))) - unit_cost)
                rate = ((nav if nav is not None else float(item.get("price", unit_cost))) / unit_cost - 1) * 100 if unit_cost else float("nan")
                row["持有收益"] = f"{profit:+,.2f}"
                row["收益率"] = f"{rate:+.2f}%"
            if screenshot_mv not in (None, ""):
                row["截图市值"] = f"{float(screenshot_mv):,.2f}"
                compare_mv = check_mv if check_mv is not None else calc_mv
                if compare_mv is not None and float(screenshot_mv) > 0:
                    gap = abs(compare_mv - float(screenshot_mv)) / float(screenshot_mv)
                    if gap > 0.01:
                        row["状态"] = "市值偏差"
                        row["是否可写入"] = "否"
                        ok = False
                        notes.append(f"算出市值与截图市值偏差 {gap:.1%}，请核对代码/份额/成本")
            if account == "支付宝" and nav_date:
                notes.append(f"按 {nav_date} 披露净值反推；QDII 净值滞后属正常")
            if ok:
                proposed_raw.append({
                    "account": account,
                    "type": htype,
                    "name": item["name"],
                    "code": code,
                    "share_class": item.get("share_class", ""),
                    "shares": round(shares, 4),
                    "unit_cost": round(unit_cost, 6),
                })

        if not ok and row["是否可写入"] == "是":
            row["是否可写入"] = "否"
        row["提示"] = "；".join(notes)
        preview.append(row)

    proposed, merge_notes = _merge_position_rows(proposed_raw)
    return preview, proposed, merge_notes

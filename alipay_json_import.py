# -*- coding: utf-8 -*-
"""支付宝持仓截图 JSON 导入：解析、匹配代码、按净值反推份额和成本。"""
import json
import re

from holding_import import clean_code
from nav_utils import get_nav, market_cache_key


ALIPAY_JSON_PROMPT = '''你是基金持仓截图解析器。我会发一张支付宝/养基宝"我的持有"截图。
请逐只基金提取，只输出一个JSON数组，不要任何解释文字、不要markdown代码块标记。
每只基金一个对象，字段：
- name: 基金全称（如"永赢高端装备智选混合A"）
- share_class: 份额类别，从名称结尾识别，A类填"A"、C类填"C"、没有填""
- amount: 当前金额/市值（数字，去掉逗号）
- profit: 持有收益（数字，亏损为负）
- profit_rate: 持有收益率（数字，百分号去掉，如-18.18填-18.18）
- account: 固定填"支付宝"
- today_updated: 该行有"今日收益更新"标记填true，否则false
找不到的数字填null。示例：
[{"name":"永赢高端装备智选混合A","share_class":"A","amount":15217.89,"profit":-3382.38,"profit_rate":-18.18,"account":"支付宝","today_updated":true}]'''


def _to_float(value, field):
    if value is None or value == "":
        raise ValueError(f"{field} 不能为空")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = text.replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    text = text.replace("元", "").replace("%", "").replace("+", "").replace("−", "-")
    return float(text)


def _to_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "已更新", "今日收益更新"}


def infer_share_class(name, explicit=""):
    explicit = str(explicit or "").strip().upper()
    if explicit in {"A", "C"}:
        return explicit
    text = str(name or "").upper().strip()
    m = re.search(r"([AC])(?:类)?(?:人民币)?$", text)
    return m.group(1) if m else ""


def normalize_fund_name(name):
    text = str(name or "").lower()
    text = re.sub(r"\([^)]*\)|（[^）]*）", "", text)
    text = text.replace("人民币", "").replace("基金", "")
    for word in ("混合型", "股票型", "债券型", "指数型", "混合", "股票", "债券", "指数", "联接", "lof", "etf", "qdii"):
        text = text.replace(word, "")
    text = re.sub(r"[ac]类?$", "", text, flags=re.I)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text)
    return text


def parse_alipay_json(text):
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
        missing = [k for k in ("name", "amount", "profit") if raw.get(k) in (None, "")]
        if missing:
            raise ValueError(f"第 {idx} 行缺少字段：{', '.join(missing)}")
        name = str(raw.get("name", "")).strip()
        share_class = infer_share_class(name, raw.get("share_class", ""))
        rows.append({
            "name": name,
            "share_class": share_class,
            "amount": _to_float(raw.get("amount"), "amount"),
            "profit": _to_float(raw.get("profit"), "profit"),
            "profit_rate": None if raw.get("profit_rate") in (None, "") else _to_float(raw.get("profit_rate"), "profit_rate"),
            "account": str(raw.get("account") or "支付宝").strip(),
            "today_updated": _to_bool(raw.get("today_updated")),
        })
    return rows


def alipay_code_options(records):
    out = []
    seen = set()
    for r in records:
        if r.get("account") == "支付宝" and r.get("type") == "otc":
            code = clean_code(r.get("code", ""))
            if code and code not in seen:
                out.append({"code": code, "name": r.get("name", ""), "share_class": infer_share_class(r.get("name", ""))})
                seen.add(code)
    return out


def match_alipay_code(item, records):
    options = alipay_code_options(records)
    item_name = normalize_fund_name(item.get("name", ""))
    item_class = item.get("share_class", "")
    scored = []
    for opt in options:
        rec_name = normalize_fund_name(opt.get("name", ""))
        rec_class = opt.get("share_class", "")
        if item_class and rec_class and item_class != rec_class:
            continue
        if not item_name or not rec_name:
            continue
        if rec_name in item_name or item_name in rec_name:
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


def build_alipay_preview(items, records, selected_codes=None):
    selected_codes = selected_codes or {}
    current_by_code = {clean_code(r.get("code", "")): r for r in records if r.get("account") == "支付宝" and r.get("type") == "otc"}
    preview = []
    proposed = []
    for idx, item in enumerate(items):
        code = clean_code(selected_codes.get(idx) or match_alipay_code(item, records))
        row = {
            "名称": item["name"],
            "类别": item.get("share_class", ""),
            "金额M": f"{item['amount']:,.2f}",
            "持有收益P": f"{item['profit']:+,.2f}",
            "反推份额": "—",
            "反推成本": "—",
            "净值+日期": "—",
            "code": code or "待选择",
            "状态": "可写入",
            "提示": "",
        }
        notes = []
        ok = True
        nav = None
        nav_date = ""

        if not code:
            ok = False
            row["状态"] = "需手选代码"
            notes.append("未能按名称匹配到基金代码")
        else:
            result = get_nav(code, "otc", item["name"], cache_key=market_cache_key())
            nav = result.nav
            nav_date = result.date or ""
            source = result.kind or result.source or ""
            row["净值+日期"] = f"{nav:.4f} / {nav_date} / {source}" if nav is not None else f"— / {result.reason or '接口失败'}"
            if nav is None or nav <= 0:
                ok = False
                row["状态"] = "净值失败"
                notes.append(result.reason or "未取到可用净值")

        if ok:
            amount = float(item["amount"])
            profit = float(item["profit"])
            if amount <= 0:
                ok = False
                row["状态"] = "金额异常"
                notes.append("金额必须大于 0")
            else:
                shares = amount / nav
                cost = (amount - profit) / shares
                check_gap = abs(shares * nav - amount) / amount if amount else 1
                row["反推份额"] = f"{shares:.4f}"
                row["反推成本"] = f"{cost:.4f}"
                if check_gap >= 0.01:
                    ok = False
                    row["状态"] = "校验异常"
                    notes.append("份额×净值与金额偏差超过 1%")
                if not item.get("today_updated"):
                    notes.append("该行可能是昨日数据，反推份额会有偏差")
                if nav_date:
                    notes.append(f"按 {nav_date} 披露净值反推")
                if ok:
                    current_name = current_by_code.get(code, {}).get("name")
                    proposed.append({
                        "account": "支付宝",
                        "type": "otc",
                        "name": current_name or item["name"],
                        "code": code,
                        "cost": round(cost, 6),
                        "shares": round(shares, 4),
                    })

        row["提示"] = "；".join(notes)
        row["是否可写入"] = "是" if ok else "否"
        preview.append(row)
    return preview, proposed

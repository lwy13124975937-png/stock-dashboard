# -*- coding: utf-8 -*-
"""持仓导入安全流程：解析、校验、差异预览、备份后写入 holdings_data.json。"""
import csv
import json
import re
import shutil
from datetime import datetime
from io import StringIO

from my_holdings import BOARD_MAP, load_holdings_data
from project_paths import BACKUP_DIR, HOLDINGS_DATA_FILE

REQUIRED_COMMON = {"account", "type", "name", "code"}
VALID_TYPES = {"stock", "lof", "otc"}
POSITION_TYPES = {"stock", "lof", "otc"}
IMPORT_META_FIELDS = {
    "confidence", "current_price", "market_value", "profit", "profit_rate",
    "available", "source", "daily_profit", "daily_rate", "board", "board_change"
}


def current_holdings():
    holdings, _ = load_holdings_data()
    return [dict(x) for x in holdings]


def current_board_map():
    _, board_map = load_holdings_data()
    return dict(board_map or BOARD_MAP)


def current_by_key():
    return {holding_key(r): dict(r) for r in current_holdings()}


def parse_records(text):
    """支持 JSON 列表 / {"holdings": [...]} / CSV 文本。"""
    text = (text or "").strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("holdings", [])
        if not isinstance(data, list):
            raise ValueError("JSON 必须是列表，或包含 holdings 列表。")
        return data

    reader = csv.DictReader(StringIO(text))
    rows = [dict(row) for row in reader]
    if not rows:
        raise ValueError("没有解析到记录。CSV 第一行必须是字段名。")
    return rows


def clean_code(code):
    s = str(code or "").strip().upper()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    return digits.zfill(6) if len(digits) <= 6 else digits


def to_float(value, field):
    if value is None or value == "":
        raise ValueError(f"{field} 不能为空")
    s = str(value).replace(",", "").replace("元", "").strip()
    return float(s)


def normalize_records(records, fill_from_current=True, default_account=None, default_type=None):
    out = []
    errors = []
    for idx, raw in enumerate(records, 1):
        try:
            r = {str(k).strip(): v for k, v in dict(raw).items()}
            if not r.get("account") and default_account:
                r["account"] = default_account
            if not r.get("type") and default_type:
                r["type"] = default_type
            missing = REQUIRED_COMMON - {k for k, v in r.items() if v not in (None, "")}
            if missing:
                raise ValueError(f"缺少字段：{', '.join(sorted(missing))}")
            item = {
                "account": str(r["account"]).strip(),
                "type": str(r["type"]).strip(),
                "name": str(r["name"]).strip(),
                "code": clean_code(r["code"]),
            }
            if item["type"] not in VALID_TYPES:
                raise ValueError("type 只能是 stock / lof / otc")
            if item["type"] in POSITION_TYPES:
                item["share_class"] = str(r.get("share_class", "") or "").strip().upper()
                item["unit_cost"] = to_float(r.get("unit_cost", r.get("cost")), "unit_cost")
                item["shares"] = to_float(r.get("shares"), "shares")
            else:
                current = current_by_key().get(holding_key(item), {}) if fill_from_current else {}
                mv = r.get("market_value")
                profit = r.get("profit")
                if mv in (None, "") and current.get("market_value") in (None, "") and r.get("shares") not in (None, "") and r.get("cost") not in (None, ""):
                    mv = to_float(r.get("shares"), "shares") * to_float(r.get("cost"), "cost")
                item["market_value"] = to_float(mv if mv not in (None, "") else current.get("market_value"), "market_value")
                item["profit"] = to_float(profit if profit not in (None, "") else current.get("profit", 0), "profit")
            for field in IMPORT_META_FIELDS:
                if field in r and r.get(field) not in (None, ""):
                    item[field] = r.get(field)
            out.append(item)
        except Exception as e:
            errors.append({"行号": idx, "错误": str(e), "原始内容": raw})
    return out, errors


def strip_import_metadata(records):
    clean = []
    for raw in records:
        r = dict(raw)
        item = {
            "account": r["account"],
            "type": r["type"],
            "name": r["name"],
            "code": r["code"],
        }
        if r.get("share_class") not in (None, ""):
            item["share_class"] = str(r.get("share_class")).strip().upper()
        else:
            item["share_class"] = ""
        unit_cost = r.get("unit_cost", r.get("cost"))
        if r["type"] in {"stock", "lof"} or (r["type"] == "otc" and ("unit_cost" in r or "cost" in r or "shares" in r)):
            item["unit_cost"] = float(unit_cost)
            item["shares"] = float(r["shares"])
        else:
            # Legacy fallback for old Alipay records before the JSON reverse-calc import.
            item["market_value"] = float(r["market_value"])
            item["profit"] = float(r["profit"])
        if r.get("buy_date"):
            item["buy_date"] = str(r.get("buy_date")).strip()
        clean.append(item)
    return clean


def holding_key(r):
    return (
        str(r.get("account", "")),
        str(r.get("type", "")),
        str(r.get("code", "")),
        str(r.get("share_class", "") or ""),
    )


def _duplicate_signature(r):
    return (
        str(r.get("account", "")),
        str(r.get("type", "")),
        str(r.get("code", "")),
        str(r.get("name", "")),
        float(r.get("shares", 0) or 0) if r.get("type") in POSITION_TYPES else "",
        float(r.get("unit_cost", r.get("cost", 0)) or 0) if r.get("type") in POSITION_TYPES else "",
        float(r.get("market_value", 0) or 0) if r.get("market_value") not in (None, "") else "",
        float(r.get("profit", 0) or 0) if r.get("profit") not in (None, "") else "",
    )


def consolidate_same_code(records):
    """同账户、同类型、同代码的多行记录合并，避免截图重复行造成重复计数。"""
    groups = {}
    order = []
    for r in records:
        key = holding_key(r)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(dict(r))

    consolidated = []
    notes = []
    for key in order:
        rows = groups[key]
        if len(rows) == 1:
            consolidated.append(rows[0])
            continue

        unique_rows = []
        seen = set()
        removed = 0
        for row in rows:
            sig = _duplicate_signature(row)
            if sig in seen:
                removed += 1
                continue
            seen.add(sig)
            unique_rows.append(row)

        first = unique_rows[0]
        if removed:
            notes.append({
                "账户": key[0],
                "类型": key[1],
                "代码": key[2],
                "名称": first.get("name", ""),
                "处理": "已去除疑似重复行",
                "说明": f"截图里有 {removed} 条完全相同的记录，已按 1 条计算。写入前仍请核对。"
            })

        if len(unique_rows) == 1:
            if removed:
                row = dict(first)
                row["confidence"] = "中，已去除疑似重复行，请核对"
                consolidated.append(row)
            else:
                consolidated.append(first)
            continue

        if first.get("type") in POSITION_TYPES:
            total_shares = sum(float(r.get("shares", 0) or 0) for r in unique_rows)
            total_cost_amount = sum(float(r.get("unit_cost", r.get("cost", 0)) or 0) * float(r.get("shares", 0) or 0) for r in unique_rows)
            merged = {
                "account": first["account"],
                "type": first["type"],
                "name": first["name"],
                "code": first["code"],
                "share_class": str(first.get("share_class", "") or ""),
                "unit_cost": round(total_cost_amount / total_shares, 4) if total_shares else float(first.get("unit_cost", first.get("cost", 0)) or 0),
                "shares": total_shares,
                "confidence": "中，已合并同代码多行，请核对数量和加权成本",
            }
            for field in ("available", "market_value", "profit"):
                values = [r.get(field) for r in unique_rows if r.get(field) not in (None, "")]
                if values:
                    try:
                        merged[field] = sum(float(v) for v in values)
                    except Exception:
                        pass
            prices = {str(r.get("current_price")) for r in unique_rows if r.get("current_price") not in (None, "")}
            if len(prices) == 1:
                merged["current_price"] = unique_rows[0].get("current_price")
            notes.append({
                "账户": key[0],
                "类型": key[1],
                "代码": key[2],
                "名称": first.get("name", ""),
                "处理": "已按加权成本合并",
                "说明": f"同一代码识别到 {len(unique_rows)} 行，数量已相加，成本按数量加权平均。"
            })
            consolidated.append(merged)
        else:
            row = dict(unique_rows[-1])
            row["confidence"] = "中，同代码多行已保留最后一行，请核对"
            notes.append({
                "账户": key[0],
                "类型": key[1],
                "代码": key[2],
                "名称": row.get("name", ""),
                "处理": "同代码多行待核对",
                "说明": "场外基金同一代码出现多行，系统暂保留最后一行。"
            })
            consolidated.append(row)

    return consolidated, notes


def _fmt_num(value, digits=3):
    try:
        v = float(value)
    except Exception:
        return str(value)
    text = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _change_text(label, old_value, new_value, field=None):
    old_text = _fmt_num(old_value)
    new_text = _fmt_num(new_value)
    if field == "shares":
        delta = float(new_value or 0) - float(old_value or 0)
        if abs(delta) > 1e-9:
            word = "加仓" if delta > 0 else "减仓"
            return f"{label} {old_text}→{new_text}（{word}{delta:+.0f}）"
    return f"{label} {old_text}→{new_text}"


def _field_equal(old_value, new_value):
    try:
        return abs(float(old_value) - float(new_value)) < 1e-9
    except Exception:
        return str(old_value or "") == str(new_value or "")


def _record_summary(r):
    if not r:
        return ""
    if r.get("type") in POSITION_TYPES and r.get("shares") not in (None, ""):
        return f"份额 {_fmt_num(r.get('shares'), 0)}；单位成本 {_fmt_num(r.get('unit_cost', r.get('cost')))}"
    return f"市值 {_fmt_num(r.get('market_value'), 2)}；收益 {_fmt_num(r.get('profit'), 2)}"


def _record_changes(old, now):
    labels = {
        "name": "名称",
        "shares": "份额",
        "unit_cost": "单位成本",
        "cost": "成本",
        "market_value": "市值",
        "profit": "收益",
    }
    fields = ["name", "shares", "unit_cost"] if now.get("type") in POSITION_TYPES and now.get("shares") not in (None, "") else ["name", "market_value", "profit"]
    parts = []
    for field in fields:
        if _field_equal(old.get(field), now.get(field)):
            continue
        if field == "name":
            parts.append(f"名称 {old.get(field, '')}→{now.get(field, '')}")
        else:
            parts.append(_change_text(labels[field], old.get(field, 0), now.get(field, 0), field=field))
    return "；".join(parts)


def diff_records(current, proposed):
    current = strip_import_metadata(current)
    proposed = strip_import_metadata(proposed)
    cur = {holding_key(r): r for r in current}
    new = {holding_key(r): r for r in proposed}
    rows = []
    for key in sorted(set(cur) | set(new)):
        old = cur.get(key)
        now = new.get(key)
        if old and not now:
            action = "删除"
            name = old.get("name", "")
            detail = f"删除：{_record_summary(old)}"
        elif now and not old:
            action = "新增"
            name = now.get("name", "")
            detail = f"新增：{_record_summary(now)}"
        elif old == now:
            action = "不变"
            name = now.get("name", "")
            detail = ""
        else:
            action = "更新"
            name = now.get("name", "")
            detail = _record_changes(old, now)
        rows.append({
            "操作": action,
            "账户": key[0],
            "类型": key[1],
            "代码": key[2],
            "名称": name,
            "变化明细": detail,
        })
    return rows


def merge_records(current, proposed, mode="replace_same_account_type"):
    """根据导入模式生成最终写入列表。

    replace_same_account_type: 只替换识别结果涉及的 account+type 组合，适合单个账户截图。
    upsert_only: 只新增/更新识别到的记录，不删除未识别记录。
    replace_all: 用识别结果整体替换 HOLDINGS。
    """
    current = strip_import_metadata(current)
    proposed = strip_import_metadata(proposed)
    if mode == "replace_all":
        return proposed

    proposed_map = {holding_key(r): r for r in proposed}
    if mode == "upsert_only":
        result_map = {holding_key(r): r for r in current}
        result_map.update(proposed_map)
        return list(result_map.values())

    scopes = {(r["account"], r["type"]) for r in proposed}
    kept = [r for r in current if (r["account"], r["type"]) not in scopes]
    return kept + proposed


def backup_holdings_file():
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"holdings_data_{stamp}.json"
    if HOLDINGS_DATA_FILE.exists():
        shutil.copy2(HOLDINGS_DATA_FILE, backup)
        return backup
    return None


def write_holdings(records, board_map=None):
    """备份后写入 holdings_data.json，返回备份路径；首次创建时返回 None。"""
    backup = backup_holdings_file()
    data = {
        "holdings": strip_import_metadata(records),
        "board_map": {str(k): list(v) if isinstance(v, tuple) else v for k, v in (board_map or current_board_map()).items()},
    }
    HOLDINGS_DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return backup


def display_records(records):
    rows = []
    for r in records:
        row = {
            "账户": r.get("account", ""),
            "类型": r.get("type", ""),
            "名称": r.get("name", ""),
            "代码": r.get("code", ""),
            "数量/份额": r.get("shares", ""),
            "类别": r.get("share_class", ""),
            "单位成本": r.get("unit_cost", r.get("cost", "")),
            "市值": r.get("market_value", ""),
            "收益": r.get("profit", ""),
            "现价": r.get("current_price", ""),
            "识别置信度": r.get("confidence", "人工待确认"),
        }
        rows.append(row)
    return rows


SAMPLE_JSON = """[
  {"account":"东方财富","type":"stock","name":"中芯国际","code":"688981","cost":85.30,"shares":100},
  {"account":"支付宝","type":"otc","name":"某场外基金","code":"012345","cost":1.234,"shares":1000}
]"""

SAMPLE_CSV = """name,code,shares,cost
中芯国际,688981,100,85.30
某场外基金,012345,1000,1.234
"""

EASTMONEY_SCREENSHOT_SAMPLE_JSON = SAMPLE_CSV
GALAXY_LOF_SCREENSHOT_SAMPLE_JSON = SAMPLE_CSV
ALIPAY_OTC_SCREENSHOT_SAMPLE_JSON = SAMPLE_CSV

FOUR_COLUMN_OCR_PROMPT = """识别这张券商/基金持仓截图，输出CSV，只输出CSV内容，不要任何解释、不要代码块符号：
第一行固定表头：name,code,shares,cost
name列：标的简称，去掉换行写成一行
code列：6位代码
shares列：持仓份额或股数（取"持仓/持股"那列）
cost列：单位成本价（取"成本"那列）
重要：同一个code若出现多行，必须合并成一行——shares相加，cost按份额加权平均后保留3位小数
"""

VISION_PROMPT_EASTMONEY_A_STOCK = FOUR_COLUMN_OCR_PROMPT
VISION_PROMPT_GALAXY_LOF = FOUR_COLUMN_OCR_PROMPT
VISION_PROMPT_ALIPAY_OTC = FOUR_COLUMN_OCR_PROMPT

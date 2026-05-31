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


def normalize_records(records, fill_from_current=True):
    out = []
    errors = []
    for idx, raw in enumerate(records, 1):
        try:
            r = {str(k).strip(): v for k, v in dict(raw).items()}
            missing = REQUIRED_COMMON - set(r)
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
            if item["type"] in {"stock", "lof"}:
                item["cost"] = to_float(r.get("cost"), "cost")
                item["shares"] = to_float(r.get("shares"), "shares")
            else:
                current = current_by_key().get(holding_key(item), {}) if fill_from_current else {}
                mv = r.get("market_value")
                profit = r.get("profit")
                item["market_value"] = to_float(mv if mv not in (None, "") else current.get("market_value"), "market_value")
                item["profit"] = to_float(profit if profit not in (None, "") else current.get("profit"), "profit")
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
        if r["type"] in {"stock", "lof"}:
            item["cost"] = float(r["cost"])
            item["shares"] = float(r["shares"])
        else:
            item["market_value"] = float(r["market_value"])
            item["profit"] = float(r["profit"])
        clean.append(item)
    return clean


def holding_key(r):
    return (str(r.get("account", "")), str(r.get("type", "")), str(r.get("code", "")))


def _duplicate_signature(r):
    return (
        str(r.get("account", "")),
        str(r.get("type", "")),
        str(r.get("code", "")),
        str(r.get("name", "")),
        float(r.get("shares", 0) or 0) if r.get("type") in {"stock", "lof"} else "",
        float(r.get("cost", 0) or 0) if r.get("type") in {"stock", "lof"} else "",
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

        if first.get("type") in {"stock", "lof"}:
            total_shares = sum(float(r.get("shares", 0) or 0) for r in unique_rows)
            total_cost_amount = sum(float(r.get("cost", 0) or 0) * float(r.get("shares", 0) or 0) for r in unique_rows)
            merged = {
                "account": first["account"],
                "type": first["type"],
                "name": first["name"],
                "code": first["code"],
                "cost": round(total_cost_amount / total_shares, 4) if total_shares else float(first.get("cost", 0) or 0),
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
            action = "将删除"
            name = old.get("name", "")
        elif now and not old:
            action = "将新增"
            name = now.get("name", "")
        elif old == now:
            action = "不变"
            name = now.get("name", "")
        else:
            action = "将修改"
            name = now.get("name", "")
        rows.append({
            "操作": action,
            "账户": key[0],
            "类型": key[1],
            "代码": key[2],
            "名称": name,
            "原记录": old or "",
            "新记录": now or "",
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
            "成本": r.get("cost", ""),
            "市值": r.get("market_value", ""),
            "收益": r.get("profit", ""),
            "现价": r.get("current_price", ""),
            "识别置信度": r.get("confidence", "人工待确认"),
        }
        rows.append(row)
    return rows


SAMPLE_JSON = """[
  {"account":"东方财富","type":"stock","name":"中芯国际","code":"688981","cost":85.30,"shares":100},
  {"account":"支付宝","type":"otc","name":"某场外基金","code":"012345","market_value":12345.67,"profit":123.45}
]"""

SAMPLE_CSV = """account,type,name,code,cost,shares,market_value,profit
东方财富,stock,中芯国际,688981,85.30,100,,
支付宝,otc,某场外基金,012345,,,12345.67,123.45
"""

EASTMONEY_SCREENSHOT_SAMPLE_JSON = """[
  {"account":"东方财富","type":"stock","name":"示例股票A","code":"600000","shares":100,"available":100,"current_price":12.340,"cost":10.000,"market_value":1234.00,"profit":234.00,"profit_rate":"23.40%","confidence":"高","source":"东方财富A股持仓截图"},
  {"account":"东方财富","type":"stock","name":"示例股票B","code":"000001","shares":200,"available":200,"current_price":8.880,"cost":9.500,"market_value":1776.00,"profit":-124.00,"profit_rate":"-6.53%","confidence":"高","source":"东方财富A股持仓截图"}
]"""

GALAXY_LOF_SCREENSHOT_SAMPLE_JSON = """[
  {"account":"银河证券","type":"lof","name":"示例ETF","code":"510300","shares":1000,"available":1000,"cost":3.500,"current_price":3.600,"market_value":3600.00,"profit":100.00,"profit_rate":"2.86%","confidence":"高","source":"银河场内基金持仓截图"},
  {"account":"银河证券","type":"lof","name":"示例LOF","code":"160000","shares":500,"available":500,"cost":1.200,"current_price":1.150,"market_value":575.00,"profit":-25.00,"profit_rate":"-4.17%","confidence":"高","source":"银河场内基金持仓截图"}
]"""

ALIPAY_OTC_SCREENSHOT_SAMPLE_JSON = """[
  {"account":"支付宝","type":"otc","name":"示例场外基金A","code":"012345","market_value":10000.00,"profit":800.00,"daily_profit":50.00,"daily_rate":"0.50%","board":"示例板块","board_change":"1.20%","profit_rate":"8.00%","confidence":"高","source":"支付宝场外基金持仓截图"},
  {"account":"支付宝","type":"otc","name":"示例场外基金B","code":"023456","market_value":5000.00,"profit":-200.00,"daily_profit":-30.00,"daily_rate":"-0.60%","board":"示例板块","board_change":"-0.80%","profit_rate":"-4.00%","confidence":"高","source":"支付宝场外基金持仓截图"}
]"""

VISION_PROMPT_EASTMONEY_A_STOCK = """你是一个持仓截图识别助手。请从东方财富 A 股持仓截图中提取持仓，输出严格 JSON 数组，不要输出解释文字。

截图列含义通常为：
- 股票/市值：第一行是股票名称，第二行是该股票市值。
- 持仓/可用：第一行是持仓数量，第二行是可用数量。
- 现价/成本：第一行是现价，第二行是成本价。
- 持仓盈亏比：第一行是持仓盈亏金额，第二行是持仓盈亏率。

输出字段：
account 固定为 "东方财富"
type 固定为 "stock"
name 股票名称
code 股票代码；截图若没有代码，请根据当前持仓名称匹配；无法确定时填空字符串
shares 持仓数量
available 可用数量
current_price 现价
cost 成本价
market_value 市值
profit 持仓盈亏金额
profit_rate 持仓盈亏率字符串
confidence 高/中/低
source 固定为 "东方财富A股持仓截图"

注意：
- 不要猜测不在截图中且无法从当前持仓匹配的代码。
- 红色数字是盈利，绿色数字是亏损，绿色负号必须保留。
- 输出必须能被 json.loads 直接解析。
"""

VISION_PROMPT_GALAXY_LOF = """你是一个持仓截图识别助手。请从银河证券“我的场内资产/持仓明细”截图中提取场内基金或 LOF 持仓，输出严格 JSON 数组，不要输出解释文字。

截图列含义通常为：
- 名称/市值：第一行是基金名称和代码，下面是市值。
- 参考盈亏：第一行是参考盈亏金额，第二行是盈亏率。
- 持仓/可用：第一行是持仓份额，第二行是可用份额。
- 成本/现价：第一行是成本价，第二行是现价。

输出字段：
account 固定为 "银河证券"
type 固定为 "lof"
name 基金名称
code 基金代码
shares 持仓份额
available 可用份额
cost 成本价
current_price 现价
market_value 市值
profit 参考盈亏金额
profit_rate 盈亏率字符串
confidence 高/中/低
source 固定为 "银河场内基金持仓截图"

注意：
- 如果截图中同一代码出现多行，不要擅自合并，逐行输出，并把 confidence 标为中，提示可能是分仓或重复行。
- 绿色负数必须保留负号。
- 输出必须能被 json.loads 直接解析。
"""

VISION_PROMPT_ALIPAY_OTC = """你是一个持仓截图识别助手。请从支付宝基金持仓截图中提取场外基金持仓，输出严格 JSON 数组，不要输出解释文字。

截图列含义通常为：
- 左侧：基金名称和基金代码。
- 当日收益：第一行是当日收益金额，第二行是当日收益率。
- 关联板块：第一行是板块涨跌幅，第二行是板块名称。
- 持有收益：第一行是累计持有收益金额，第二行是累计收益率。

输出字段：
account 固定为 "支付宝"
type 固定为 "otc"
name 基金名称，截图省略号时尽量根据代码和当前持仓匹配完整名称
code 基金代码
profit 累计持有收益金额
daily_profit 当日收益金额
daily_rate 当日收益率字符串
board 关联板块名称
board_change 关联板块涨跌幅字符串
profit_rate 累计收益率字符串
confidence 高/中/低
source 固定为 "支付宝场外基金持仓截图"

注意：
- 该截图通常没有每只基金市值。不要编造 market_value；留空即可，系统会沿用当前配置里的市值。
- 截图底部露出不完整的行，confidence 标为低。
- 输出必须能被 json.loads 直接解析。
"""

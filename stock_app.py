# -*- coding: utf-8 -*-
"""个人持仓 + 板块情绪看盘台。只做客观数据展示，非买卖建议。"""
import os
for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)
os.environ["NO_PROXY"]="*"; os.environ["no_proxy"]="*"

import base64
import html
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import akshare as ak
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

from account_import import (
    ACCOUNT_IMPORT_PROMPTS,
    ACCOUNT_IMPORT_SAMPLES,
    account_code_options,
    build_account_preview,
    match_code,
    parse_account_json,
)
from alipay_json_import import (
    ALIPAY_JSON_PROMPT,
    alipay_code_options,
    build_alipay_preview,
    match_alipay_code,
    parse_alipay_json,
)
from holding_import import (
    ALIPAY_OTC_SCREENSHOT_SAMPLE_JSON,
    EASTMONEY_SCREENSHOT_SAMPLE_JSON,
    GALAXY_LOF_SCREENSHOT_SAMPLE_JSON,
    SAMPLE_CSV,
    SAMPLE_JSON,
    VISION_PROMPT_ALIPAY_OTC,
    VISION_PROMPT_EASTMONEY_A_STOCK,
    VISION_PROMPT_GALAXY_LOF,
    consolidate_same_code,
    current_holdings,
    diff_records,
    display_records,
    merge_records,
    normalize_records,
    parse_records,
    write_holdings,
)
from my_holdings import load_holdings_data
from nav_utils import calc_daily_return, classify_fund, get_nav, market_cache_key
from project_paths import BOARD_HEAT_HISTORY_FILE, DB, FUND_BOARD_MAP_FILE, HOLDINGS_DATA_FILE, SNAPSHOTS_FILE
from term_help import RISK_TIP, render_glossary, render_term, risk_notice


TODAY = datetime.now().strftime("%Y%m%d")
HOLDINGS, BOARD_MAP = load_holdings_data()

st.set_page_config(page_title="我的全资产管理台", layout="wide")
st.markdown(
    """
    <style>
    :root {
        --bg: #f6f8fb;
        --card: #ffffff;
        --line: #e7edf5;
        --text: #172033;
        --muted: #697386;
        --red: #d8342a;
        --green: #159447;
        --blue: #2563eb;
        --amber: #b7791f;
    }
    .stApp { background: var(--bg); color: var(--text); }
    .block-container { padding-top: 1.75rem; padding-bottom: 2.5rem; max-width: 980px; }
    h1, h2, h3 { letter-spacing: 0; line-height: 1.25; }
    h1 { padding-top: .15rem; margin-bottom: .85rem; }
    div[data-testid="stMetric"],
    .soft-card {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: 0 8px 22px rgba(15, 23, 42, .05);
        padding: 14px 16px;
    }
    .metric-card {
        background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
        border: 1px solid var(--line);
        border-radius: 16px;
        box-shadow: 0 8px 22px rgba(15, 23, 42, .06);
        padding: 14px 16px;
        min-height: 96px;
        margin-bottom: 10px;
    }
    .metric-label { color: var(--muted); font-size: 13px; margin-bottom: 7px; }
    .metric-value { color: var(--text); font-size: 25px; line-height: 1.18; font-weight: 800; word-break: break-word; }
    .metric-sub { color: var(--muted); font-size: 13px; margin-top: 7px; }
    .compact-metric-card {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: 0 8px 22px rgba(15, 23, 42, .05);
        padding: 12px 14px;
        min-height: 76px;
        margin-bottom: 8px;
    }
    .compact-metric-label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .compact-metric-value { color: var(--text); font-size: 20px; line-height: 1.22; font-weight: 800; word-break: break-word; }
    .radar-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 6px;
        margin: 6px 0 8px;
    }
    .radar-mini {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 7px 8px;
        min-height: 52px;
    }
    .radar-mini-label { color: var(--muted); font-size: 11px; line-height: 1.1; }
    .radar-mini-value { color: var(--text); font-size: 15px; line-height: 1.18; font-weight: 800; margin-top: 5px; word-break: break-word; }
    .score-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        border: 1px solid var(--line);
        border-radius: 10px;
        overflow: hidden;
        margin-top: 6px;
    }
    .score-cell {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        padding: 7px 9px;
        border-bottom: 1px solid var(--line);
        font-size: 13px;
    }
    .score-cell:nth-child(odd) { border-right: 1px solid var(--line); }
    .score-name { color: var(--muted); }
    .score-value { font-weight: 800; }
    .diff-row {
        border: 1px solid var(--line);
        border-left-width: 5px;
        border-radius: 12px;
        padding: 10px 12px;
        margin: 8px 0;
        background: var(--card);
    }
    .diff-update { border-left-color: var(--amber); background: #fffaf0; }
    .diff-add { border-left-color: var(--red); background: #fff7f7; }
    .diff-delete { border-left-color: var(--green); background: #f4fff8; }
    .diff-same { border-left-color: #cbd5e1; opacity: .62; }
    .diff-title { font-weight: 800; margin-bottom: 4px; }
    .diff-detail { color: var(--text); font-size: 13px; line-height: 1.45; }
    .pos { color: var(--red) !important; }
    .neg { color: var(--green) !important; }
    .flat { color: var(--muted) !important; }
    .pill {
        display: inline-block;
        padding: 3px 8px;
        border-radius: 999px;
        background: #eef4ff;
        color: #2452b8;
        font-size: 12px;
        font-weight: 700;
    }
    .mini-row {
        display: grid;
        grid-template-columns: 1.35fr .75fr .75fr .75fr;
        gap: 8px;
        align-items: center;
        padding: 10px 0;
        border-bottom: 1px solid var(--line);
        font-size: 14px;
    }
    .mini-head { color: var(--muted); font-weight: 700; }
    .holding-card {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 14px;
        box-shadow: 0 6px 18px rgba(15, 23, 42, .04);
        padding: 13px 14px;
        margin: 10px 0;
    }
    .holding-title { font-weight: 800; margin-bottom: 4px; }
    .holding-meta { color: var(--muted); font-size: 13px; }
    .holding-list-head,
    .holding-list-row {
        display: grid;
        grid-template-columns: minmax(0, 1.36fr) minmax(58px, .72fr) minmax(66px, .8fr) minmax(68px, .82fr);
        gap: 6px;
        align-items: center;
    }
    .holding-list-head {
        color: var(--muted);
        font-size: 12px;
        font-weight: 700;
        padding: 6px 0 8px;
        border-bottom: 1px solid var(--line);
    }
    .holding-list-head > div,
    .holding-list-head a {
        min-width: 0;
        text-align: right;
        color: var(--muted) !important;
        text-decoration: none !important;
    }
    .holding-list-head > div:first-child {
        text-align: left;
    }
    .holding-list-row {
        background: transparent;
        border-bottom: 1px solid var(--line);
        padding: 10px 0;
        margin: 0;
    }
    .holding-name-link {
        display: block;
        color: var(--text) !important;
        text-decoration: none !important;
        font-size: 15px;
        font-weight: 850;
        line-height: 1.22;
        white-space: normal;
        word-break: break-word;
    }
    .holding-list-meta {
        color: var(--muted);
        font-size: 12px;
        margin-top: 7px;
        line-height: 1.2;
        white-space: nowrap;
    }
    .holding-cell {
        min-width: 0;
        text-align: right;
    }
    .holding-list-value {
        font-weight: 850;
        line-height: 1.15;
        word-break: break-word;
        text-align: right;
        font-size: 14px;
    }
    .holding-list-sub {
        color: var(--muted);
        font-size: 12px;
        margin-top: 4px;
        line-height: 1.15;
        text-align: right;
        word-break: break-word;
    }
    .holding-topbar {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-bottom: 10px;
    }
    .holding-topitem {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 9px 10px;
        min-height: 58px;
    }
    .holding-toplabel { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .holding-topvalue { font-size: 19px; font-weight: 850; line-height: 1.15; }
    .account-title {
        font-size: 20px;
        font-weight: 850;
        margin: 14px 0 4px;
        color: var(--text);
    }
    .holding-subtotal {
        background: transparent;
        border-bottom: 1px solid var(--line);
        padding: 8px 0 13px;
        margin: 0 0 10px;
        color: var(--muted);
        font-size: 12px;
    }
    .kv-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        margin-top: 10px;
    }
    .kv { background: #f8fafc; border-radius: 10px; padding: 8px; }
    .kv-label { color: var(--muted); font-size: 12px; }
    .kv-value { font-weight: 800; margin-top: 2px; }
    div[data-testid="stDataFrame"] { font-size: 13px; }
    @media (max-width: 760px) {
        .block-container { padding-top: 2rem; padding-left: .72rem; padding-right: .72rem; }
        .metric-card { min-height: 84px; padding: 12px; }
        .metric-value { font-size: 21px; }
        .compact-metric-card { min-height: 64px; padding: 10px; }
        .compact-metric-value { font-size: 16px; }
        .radar-strip { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; }
        .radar-mini { min-height: 48px; padding: 6px 5px; border-radius: 9px; }
        .radar-mini-label { font-size: 10px; }
        .radar-mini-value { font-size: 13px; }
        .score-cell { font-size: 12px; padding: 6px 7px; }
        .kv-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .mini-row { grid-template-columns: 1.25fr .65fr .65fr .75fr; gap: 6px; font-size: 13px; }
        .holding-list-head,
        .holding-list-row { grid-template-columns: minmax(0, 1.32fr) minmax(54px, .68fr) minmax(62px, .76fr) minmax(64px, .8fr); gap: 4px; }
        .holding-list-head { font-size: 11px; }
        .holding-list-row { padding: 9px 0; }
        .holding-name-link { font-size: 14px; }
        .holding-list-meta, .holding-list-sub { font-size: 11px; }
        .holding-list-value { font-size: 13px; }
        .holding-topvalue { font-size: 17px; }
        .account-title { font-size: 18px; margin: 12px 0 3px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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

FUND_BOARD_OVERRIDES = {
    # These are domestic A-share themed funds. Keep the display semantic even
    # when the penetration cache/database is missing or too granular.
    "015789": ("军工装备", "军工装备"),
    "022755": ("A股量化", "元件"),
    "160221": ("有色金属", "小金属"),
}
MISSING_FUND_BOARD = "__FUND_PENETRATION_MISSING__"
METAL_BOARDS = {"贵金属", "工业金属", "小金属", "能源金属"}

ACCOUNT_KEYS = {
    "银河证券": "galaxy",
    "东方财富": "eastmoney",
    "支付宝": "alipay",
}


def holding_snapshot_id(account, htype, code, share_class=""):
    account_key = ACCOUNT_KEYS.get(str(account), re.sub(r"\W+", "_", str(account)).strip("_") or "account")
    type_key = re.sub(r"\W+", "_", str(htype)).strip("_") or "asset"
    code_key = re.sub(r"\W+", "_", str(code)).strip("_") or "code"
    class_key = re.sub(r"\W+", "_", str(share_class or "")).strip("_")
    return f"{account_key}_{type_key}_{code_key}_{class_key}" if class_key else f"{account_key}_{type_key}_{code_key}"


def detail_key_for_row(r):
    return f"{r.get('account', '')}|{r.get('type', '')}|{r.get('code', '')}|{r.get('share_class', '') or ''}"


def unit_cost_of(row):
    return row.get("unit_cost", row.get("cost", 0))


def signed_money(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:+,.0f} 元"
    except Exception:
        return "—"


def signed_pct(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:+.2f}%"
    except Exception:
        return "—"


def pct_text(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:.1f}%"
    except Exception:
        return "—"


def china_today_date():
    return pd.Timestamp(datetime.utcnow() + timedelta(hours=8)).normalize()


def china_today_string():
    return china_today_date().strftime("%Y-%m-%d")


def clean_stock_code(code):
    digits = re.sub(r"\D", "", str(code or ""))
    return digits.zfill(6) if 0 < len(digits) <= 6 else digits


def is_a_share_code(code):
    code = clean_stock_code(code)
    return len(code) == 6 and code[0] in "03689"

PAGE_TERMS = {
    "home": ["估算值", "情绪温度分", "高温板块持仓占比", "数据可信度"],
    "holding": ["单一持仓集中度", "K线", "MACD", "RSI", "单位净值", "日增长率"],
    "radar": ["板块温度", "情绪温度分", "低温升温", "高位过热", "高位降温", "当日低温", "强势延续", "中性观察", "数据可信度", "涨跌幅", "净流入", "上涨家数占比", "成交额", "趋势箭头"],
    "mine": ["基金穿透", "板块占比", "资产暴露", "情绪温度分", "高温板块持仓占比", "低温板块持仓占比", "境外/非A股"],
    "advanced": ["AI识别结果", "识别置信度", "人工确认", "自动备份", "差异预览", "接口失败", "重试次数", "数据可信度"],
}

IMPORT_TEMPLATES = {
    "东方财富A股持仓截图": {
        "account": "东方财富",
        "type": "stock",
        "sample": EASTMONEY_SCREENSHOT_SAMPLE_JSON,
        "prompt": VISION_PROMPT_EASTMONEY_A_STOCK,
        "note": "只更新东方财富 A 股个股，不影响银河、支付宝或场内基金。",
    },
    "银河场内基金持仓截图": {
        "account": "银河证券",
        "type": "lof",
        "sample": GALAXY_LOF_SCREENSHOT_SAMPLE_JSON,
        "prompt": VISION_PROMPT_GALAXY_LOF,
        "note": "只更新银河证券场内基金/LOF。同一代码多行会先合并或去重，并提示核对。",
    },
    "支付宝场外基金持仓截图": {
        "account": "支付宝",
        "type": "otc",
        "sample": ALIPAY_OTC_SCREENSHOT_SAMPLE_JSON,
        "prompt": VISION_PROMPT_ALIPAY_OTC,
        "note": "只更新支付宝场外基金。截图缺少单只基金市值时，会沿用当前配置里的市值。",
    },
}


def to_num(x):
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "nan", "None"):
        return float("nan")
    m = 1
    if s.endswith("亿"):
        m = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        m = 1e4
        s = s[:-1]
    try:
        return float(s) * m
    except Exception:
        return float("nan")


def fmt_money(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:,.0f} 元"
    except Exception:
        return "—"


def fmt_price(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:.3f}"
    except Exception:
        return "—"


def fmt_pct(v):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        return f"{n:.1f}%"
    except Exception:
        return "—"


def cls(v):
    try:
        n = float(v)
    except Exception:
        return "flat"
    if n > 0:
        return "pos"
    if n < 0:
        return "neg"
    return "flat"


def esc(x):
    return html.escape(str(x))


def value_html(v, suffix="", signed=False):
    try:
        n = float(v)
        if pd.isna(n):
            return "—"
        text = f"{n:+,.0f}{suffix}" if signed else f"{n:,.0f}{suffix}"
        return f'<span class="{cls(n)}">{esc(text)}</span>'
    except Exception:
        return esc(v)


def card(label, value, sub="", tone="flat"):
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{esc(label)}</div>
            <div class="metric-value {tone}">{value}</div>
            <div class="metric-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def mini_table(rows):
    if not rows:
        st.caption("暂无数据。")
        return
    st.markdown(
        '<div class="soft-card">'
        '<div class="mini-row mini-head"><div>名称</div><div>温度</div><div>变化</div><div>状态</div></div>',
        unsafe_allow_html=True,
    )
    for r in rows:
        score = "—" if pd.isna(r.get("情绪温度分")) else f"{float(r.get('情绪温度分')):.0f}"
        delta = "—" if pd.isna(r.get("温度变化")) else f"{float(r.get('温度变化')):+.1f}"
        st.markdown(
            f"""
            <div class="mini-row">
                <div><b>{esc(r.get('板块', ''))}</b></div>
                <div>{esc(score)}</div>
                <div class="{cls(r.get('温度变化'))}">{esc(delta)}</div>
                <div>{esc(r.get('情绪标签', ''))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def metric_with_help(col, label, value, delta=None, term=None):
    with col:
        st.metric(label, value, delta)
        if term:
            render_term(st, term)


def compact_metric_with_help(col, label, value, term=None):
    with col:
        st.markdown(
            f"""
            <div class="compact-metric-card">
                <div class="compact-metric-label">{esc(label)}</div>
                <div class="compact-metric-value">{esc(value)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if term:
            render_term(st, term)


def radar_metric_strip(items):
    cols = st.columns(4)
    for col, (label, value) in zip(cols, items):
        with col:
            with st.container(border=True):
                st.caption(label)
                st.markdown(f"**{value}**")


def score_breakdown_grid(row):
    rows = score_breakdown(row).to_dict("records")
    for i in range(0, len(rows), 2):
        cols = st.columns(2)
        for col, r in zip(cols, rows[i:i + 2]):
            with col:
                st.caption(r["维度"])
                st.markdown(f"**{r['得分']}**")


def render_diff_preview(rows):
    if not rows:
        st.info("没有可预览的差异。")
        return
    class_map = {"更新": "diff-update", "新增": "diff-add", "删除": "diff-delete", "不变": "diff-same"}
    icon_map = {"更新": "●", "新增": "+", "删除": "-", "不变": "·"}
    for r in rows:
        action = str(r.get("操作", ""))
        detail = r.get("变化明细") or "字段无变化"
        st.markdown(
            f"""
            <div class="diff-row {class_map.get(action, '')}">
                <div class="diff-title">{esc(icon_map.get(action, '·'))} {esc(action)}｜{esc(r.get("名称", ""))} {esc(r.get("代码", ""))}</div>
                <div class="diff-detail">{esc(r.get("账户", ""))} / {esc(r.get("类型", ""))}｜{esc(detail)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def optional_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
    except Exception:
        return default
    return value if value is not None else default


def required_secret(name):
    try:
        value = st.secrets[name]
    except KeyError:
        raise RuntimeError(f"未读取到 {name}，请检查 Streamlit Secrets 配置")
    except Exception as e:
        raise RuntimeError(f"读取 {name} 失败：{type(e).__name__}：{e}")
    value = str(value).strip()
    if not value:
        raise RuntimeError(f"{name} 是空的，请检查 Streamlit Secrets 配置")
    return value


def github_message(resp):
    try:
        data = resp.json()
    except Exception:
        return resp.text[:220]
    message = str(data.get("message", "")).strip()
    if not message and resp.status_code < 400:
        return "OK"
    errors = data.get("errors", [])
    if errors:
        detail = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors[:2])
        message = f"{message}：{detail}" if message else detail
    return message[:260] or "GitHub 返回了未知错误"


def set_github_sync_diag(diag):
    safe = {
        "是否读到 GH_TOKEN": diag.get("token_read", "否"),
        "仓库": diag.get("repo", ""),
        "分支": diag.get("branch", ""),
        "文件": diag.get("path", ""),
        "GET 状态码": diag.get("get_status", "未请求"),
        "GET message": diag.get("get_message", ""),
        "PUT 状态码": diag.get("put_status", "未请求"),
        "PUT message": diag.get("put_message", ""),
        "异常": diag.get("exception", ""),
    }
    st.session_state["github_sync_diag"] = safe


def render_github_sync_diag(expanded=False):
    diag = st.session_state.get("github_sync_diag")
    with st.expander("同步诊断", expanded=expanded):
        if not diag:
            st.caption("还没有执行过 GitHub 同步。")
        else:
            st.dataframe(
                pd.DataFrame([{"项目": k, "内容": v} for k, v in diag.items()]),
                use_container_width=True,
                hide_index=True,
            )


def push_holdings_to_github(json_text):
    owner = optional_secret("GH_OWNER", "lwy13124975937-png")
    repo = optional_secret("GH_REPO", "stock-dashboard")
    branch = optional_secret("GH_BRANCH", "main")
    path = optional_secret("GH_PATH", "holdings_data.json")
    diag = {
        "token_read": "否",
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "path": path,
        "get_status": "未请求",
        "get_message": "",
        "put_status": "未请求",
        "put_message": "",
        "exception": "",
    }
    try:
        token = required_secret("GH_TOKEN")
        diag["token_read"] = "是"
    except Exception as e:
        diag["exception"] = f"{type(e).__name__}：{e}"
        set_github_sync_diag(diag)
        return False, str(e)

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        get_resp = requests.get(url, headers=headers, params={"ref": branch}, timeout=20)
        diag["get_status"] = get_resp.status_code
        diag["get_message"] = github_message(get_resp)
        sha = None
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")
        elif get_resp.status_code != 404:
            set_github_sync_diag(diag)
            return False, f"GitHub GET 返回 {get_resp.status_code}：{diag['get_message']}"

        payload = {
            "message": "update holdings via app",
            "content": base64.b64encode(json_text.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        put_resp = requests.put(url, headers=headers, json=payload, timeout=25)
        diag["put_status"] = put_resp.status_code
        diag["put_message"] = github_message(put_resp)
        set_github_sync_diag(diag)
        if put_resp.status_code in (200, 201):
            return True, ""
        return False, f"GitHub PUT 返回 {put_resp.status_code}：{diag['put_message']}"
    except Exception as e:
        diag["exception"] = f"{type(e).__name__}：{e}"
        set_github_sync_diag(diag)
        return False, f"同步过程异常：{type(e).__name__}：{e}"


def render_performance_curve(history, scope="total"):
    if history is None or len(history) < 2:
        st.info("数据积累中，明日起逐步生成曲线。曲线从系统开始记录之日起，历史无法补全。")
        return
    prefix_map = {
        "total": ("总资产", "total_return_pct", "total_profit"),
        "galaxy": ("银河证券", "galaxy_return_pct", "galaxy_profit"),
        "eastmoney": ("东方财富", "eastmoney_return_pct", "eastmoney_profit"),
        "alipay": ("支付宝", "alipay_return_pct", "alipay_profit"),
    }
    label, return_col, profit_col = prefix_map[scope]
    missing = [c for c in ("date", return_col, profit_col, "hs300_close") if c not in history.columns]
    if missing:
        st.info("历史文件字段不完整，暂时无法生成收益曲线。")
        return

    mode = st.segmented_control(
        "曲线类型",
        ["收益率曲线", "收益金额曲线"],
        default="收益率曲线",
        key=f"curve_mode_{scope}",
        label_visibility="collapsed",
    )
    dfh = history.dropna(subset=["date"]).copy()
    if len(dfh) < 2:
        st.info("数据积累中，明日起逐步生成曲线。曲线从系统开始记录之日起，历史无法补全。")
        return

    fig = go.Figure()
    if mode == "收益金额曲线":
        fig.add_trace(go.Scatter(x=dfh["date"], y=pd.to_numeric(dfh[profit_col], errors="coerce"), name=f"{label}收益金额", mode="lines+markers"))
        fig.update_yaxes(title="收益金额（元）")
        st.caption("收益金额曲线从系统开始记录之日起展示，历史无法补全。")
    else:
        returns_raw = pd.to_numeric(dfh[return_col], errors="coerce")
        base_return = returns_raw.dropna().iloc[0] if returns_raw.notna().any() else 0
        returns = returns_raw - base_return
        hs300 = pd.to_numeric(dfh["hs300_close"], errors="coerce")
        base = hs300.dropna().iloc[0] if hs300.notna().any() else None
        hs300_ret = (hs300 / base - 1) * 100 if base else pd.Series([float("nan")] * len(dfh))
        fig.add_trace(go.Scatter(x=dfh["date"], y=returns, name=f"{label}收益率", mode="lines+markers"))
        fig.add_trace(go.Scatter(x=dfh["date"], y=hs300_ret, name="沪深300基准", mode="lines+markers"))
        last_port = returns.dropna().iloc[-1] if returns.notna().any() else float("nan")
        last_idx = hs300_ret.dropna().iloc[-1] if hs300_ret.notna().any() else float("nan")
        if pd.notna(last_port) and pd.notna(last_idx):
            diff = last_port - last_idx
            st.caption(f"从记录日起，{label}相对沪深300：{'跑赢' if diff >= 0 else '跑输'} {abs(diff):.1f}%。收益率曲线按第一条记录归零，历史无法补全。")
        fig.update_yaxes(title="收益率（%）")
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True)


def sina_stock(code):
    return ("sh" if code.startswith("6") else "sz") + code


def sina_fund(code):
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


@st.cache_data(ttl=180)
def sina_realtime_quotes(codes):
    clean_codes = [clean_stock_code(c) for c in codes if is_a_share_code(c)]
    clean_codes = list(dict.fromkeys(clean_codes))
    if not clean_codes:
        return {}
    symbols = ",".join(sina_stock(code) for code in clean_codes)
    url = f"https://hq.sinajs.cn/list={symbols}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    resp = requests.get(url, headers=headers, timeout=12)
    resp.raise_for_status()
    resp.encoding = "gbk"
    out = {}
    for line in resp.text.splitlines():
        m = re.match(r'var hq_str_(sh|sz)(\d{6})="(.*)";', line.strip())
        if not m:
            continue
        code = m.group(2)
        parts = m.group(3).split(",")
        if len(parts) < 4 or not parts[0]:
            continue
        try:
            prev = float(parts[2])
            price = float(parts[3])
            if price <= 0:
                price = float(parts[1])
            out[code] = {
                "name": parts[0],
                "prev": prev,
                "price": price,
                "date": parts[30] if len(parts) > 30 else "",
                "time": parts[31] if len(parts) > 31 else "",
            }
        except Exception:
            continue
    return out


def normalize_board_name(board):
    board = str(board or "").strip()
    if not board:
        return board
    return BOARD_ALIASES.get(board, board.replace("Ⅱ", ""))


def valid_board_name(x):
    s = str(x or "").strip()
    return s not in ("", "None", "nan", MISSING_FUND_BOARD) and not s.startswith("无")


def is_true_non_a_fund(code):
    kind, _ = classify_fund(clean_stock_code(code), "otc")
    return kind == "场外基金-QDII/境外"


def fund_board_override(code):
    return FUND_BOARD_OVERRIDES.get(clean_stock_code(code))


@st.cache_data(ttl=600)
def load_snapshot_history():
    if not SNAPSHOTS_FILE.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(SNAPSHOTS_FILE)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600)
def price_hist(code, is_fund):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
    df = None
    last = None
    for i in range(4):
        try:
            if is_fund:
                df = ak.fund_etf_hist_sina(symbol=sina_fund(code))
            else:
                df = ak.stock_zh_a_daily(symbol=sina_stock(code), start_date=start, end_date=end, adjust="qfq")
            if df is not None and len(df) > 20:
                break
        except Exception as e:
            last = e
            time.sleep(2)
    if df is None or len(df) == 0:
        raise RuntimeError(f"数据源暂时拉不到：{last}")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)
    c = df["close"]
    df["MA5"] = c.rolling(5).mean()
    df["MA10"] = c.rolling(10).mean()
    df["MA20"] = c.rolling(20).mean()
    df["MA60"] = c.rolling(60).mean()
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    df["DIF"] = e12 - e26
    df["DEA"] = df["DIF"].ewm(span=9, adjust=False).mean()
    df["MACD动能"] = 2 * (df["DIF"] - df["DEA"])
    d = c.diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    df["RSI"] = 100 - 100 / (1 + g / l.replace(0, 1e-9))
    return df


@st.cache_data(ttl=600)
def otc_nav(code):
    for i in range(3):
        try:
            return ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        except Exception:
            time.sleep(2)
    return None


@st.cache_data(ttl=600)
def eastmoney_otc_latest_nav(code):
    """天天基金最新已公布净值。只取 dwjz，不使用盘中估值 gsz。"""
    url = f"https://fundgz.1234567.com.cn/js/{str(code)}.js"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://fund.eastmoney.com/",
    }
    last = None
    for i in range(3):
        try:
            resp = requests.get(url, headers=headers, params={"rt": int(time.time() * 1000)}, timeout=10)
            resp.raise_for_status()
            m = re.search(r"jsonpgz\((.*)\);?", resp.text.strip())
            if not m:
                raise RuntimeError("返回内容不是 JSONP")
            data = json.loads(m.group(1))
            nav = pd.to_numeric(data.get("dwjz"), errors="coerce")
            nav_date = str(data.get("jzrq") or "").strip()
            if pd.isna(nav) or not nav_date:
                raise RuntimeError("缺少 dwjz 或 jzrq")
            return float(nav), nav_date, ""
        except Exception as e:
            last = e
            if i < 2:
                time.sleep(1)
    return float("nan"), "", f"天天基金最新净值接口失败：{str(last)[:60]}"


def akshare_otc_latest_nav(code):
    df = otc_nav(str(code))
    if df is None or len(df) == 0:
        return float("nan"), "", "AkShare 净值接口无返回"
    try:
        data = df.copy()
        date_col = "净值日期" if "净值日期" in data.columns else data.columns[0]
        nav_col = "单位净值" if "单位净值" in data.columns else data.columns[1]
        data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
        data[nav_col] = pd.to_numeric(data[nav_col], errors="coerce")
        data = data.dropna(subset=[date_col, nav_col]).sort_values(date_col)
        if len(data) == 0:
            return float("nan"), "", "AkShare 返回数据为空"
        row = data.iloc[-1]
        return float(row[nav_col]), str(row[date_col].date()), ""
    except Exception as e:
        return float("nan"), "", f"AkShare 净值解析失败：{str(e)[:60]}"


def quote_date_key(date_text):
    try:
        dt = pd.to_datetime(date_text, errors="coerce")
        return pd.Timestamp.min if pd.isna(dt) else dt
    except Exception:
        return pd.Timestamp.min


def latest_otc_quote(code):
    nav, nav_date, fast_error = eastmoney_otc_latest_nav(str(code))
    ak_nav, ak_date, ak_error = akshare_otc_latest_nav(str(code))
    candidates = []
    if pd.notna(nav):
        candidates.append(("天天基金", nav, nav_date))
    if pd.notna(ak_nav):
        candidates.append(("AkShare", ak_nav, ak_date))
    if candidates:
        source, best_nav, best_date = sorted(candidates, key=lambda x: quote_date_key(x[2]))[-1]
        if source == "AkShare" and pd.notna(nav):
            return best_nav, best_date, "天天基金净值日期较旧，已采用 AkShare 更新日期"
        if source == "AkShare" and fast_error:
            return best_nav, best_date, f"{fast_error}，已回退 AkShare 单位净值"
        return best_nav, best_date, ""
    return float("nan"), "", f"净值暂未获取：{fast_error}；{ak_error}"


def otc_nav_history_frame(code):
    nav = otc_nav(str(code))
    if nav is None or len(nav) == 0:
        return pd.DataFrame()
    df = nav.copy()
    date_col = "净值日期" if "净值日期" in df.columns else df.columns[0]
    nav_col = "单位净值" if "单位净值" in df.columns else df.columns[1]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df[nav_col] = pd.to_numeric(df[nav_col], errors="coerce")
    df = df.dropna(subset=[date_col, nav_col]).sort_values(date_col)
    return df.rename(columns={date_col: "日期", nav_col: "单位净值"})[["日期", "单位净值"]]


def otc_true_nav_change(code, latest_nav, latest_date):
    if pd.isna(latest_nav):
        return float("nan")
    df = otc_nav_history_frame(code)
    if len(df) < 2:
        return float("nan")
    latest_ts = pd.to_datetime(latest_date, errors="coerce")
    if pd.isna(latest_ts):
        latest_ts = df["日期"].iloc[-1]
    prev = df[df["日期"] < latest_ts]
    if len(prev) == 0:
        prev = df.iloc[:-1]
    if len(prev) == 0:
        return float("nan")
    prev_nav = float(prev.iloc[-1]["单位净值"])
    return (float(latest_nav) / prev_nav - 1) * 100 if prev_nav else float("nan")


@st.cache_data(ttl=900)
def fund_top_a_share_estimate(code):
    """按基金前十大 A 股重仓估算当日净值影响；仅用于 OTC A 股主题基金。"""
    last = None
    df = None
    for year in ("2026", "2025"):
        try:
            df = ak.fund_portfolio_hold_em(symbol=str(code), date=year)
            if df is not None and len(df):
                break
        except Exception as e:
            last = e
            time.sleep(1)
    if df is None or len(df) == 0:
        return float("nan"), pd.DataFrame(), f"重仓估值暂不可用：{str(last)[:60] if last else '无持仓'}"
    if "季度" in df.columns:
        latest = sorted(df["季度"].dropna().unique())[-1]
        df = df[df["季度"] == latest]
    df = df.copy().head(10)
    df["占净值比例"] = pd.to_numeric(df["占净值比例"], errors="coerce")
    a_share_codes = []
    for _, h in df.iterrows():
        scode = clean_stock_code(h.get("股票代码", ""))
        if is_a_share_code(scode):
            a_share_codes.append(scode)
    quotes = {}
    try:
        quotes = sina_realtime_quotes(a_share_codes)
    except Exception:
        quotes = {}
    rows = []
    estimate_pct = 0.0
    matched_weight = 0.0
    for _, h in df.iterrows():
        scode = clean_stock_code(h.get("股票代码", ""))
        if not is_a_share_code(scode):
            continue
        weight = float(h.get("占净值比例", 0) or 0)
        if weight <= 0:
            continue
        try:
            quote = quotes.get(scode)
            if quote:
                close = float(quote["price"])
                prev = float(quote["prev"])
            else:
                hist = price_hist(scode, False)
                close = float(hist.iloc[-1]["close"])
                prev = float(hist.iloc[-2]["close"]) if len(hist) > 1 else close
            change_pct = (close / prev - 1) * 100 if prev else 0.0
            contribution = weight / 100 * change_pct
            estimate_pct += contribution
            matched_weight += weight
            rows.append({
                "股票": str(h.get("股票名称", "")),
                "代码": scode,
                "权重": f"{weight:.2f}%",
                "涨跌": f"{change_pct:+.2f}%",
                "贡献": f"{contribution:+.2f}%",
            })
        except Exception:
            continue
    if matched_weight < 5:
        return float("nan"), pd.DataFrame(rows), "A股重仓权重不足，暂不估算"
    return estimate_pct, pd.DataFrame(rows), f"按前十大 A 股重仓估算，匹配权重 {matched_weight:.1f}%"


@st.cache_data(ttl=600)
def boards_live():
    for i in range(4):
        try:
            df = ak.stock_board_industry_summary_ths()
            if df is not None and len(df):
                return df, "同花顺实时接口", False
        except Exception:
            time.sleep(2)
    try:
        conn = sqlite3.connect(DB)
        latest = conn.execute("SELECT MAX(snapshot_date) FROM board_heat").fetchone()[0]
        df = pd.read_sql("SELECT * FROM board_heat WHERE snapshot_date=?", conn, params=(latest,))
        conn.close()
        if len(df):
            return df, f"本地历史快照 {latest}", True
    except Exception:
        pass
    raise RuntimeError("同花顺板块行情暂时拉不到，本地也没有历史快照")


@st.cache_data(ttl=600)
def load_board_history():
    if BOARD_HEAT_HISTORY_FILE.exists():
        try:
            return pd.read_csv(BOARD_HEAT_HISTORY_FILE, dtype={"snapshot_date": str})
        except Exception:
            pass
    try:
        conn = sqlite3.connect(DB)
        df = pd.read_sql("SELECT * FROM board_heat", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600)
def load_update_log():
    try:
        conn = sqlite3.connect(DB)
        df = pd.read_sql("SELECT * FROM update_log ORDER BY id DESC LIMIT 200", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600)
def load_fund_board_map():
    frames = []
    try:
        conn = sqlite3.connect(DB)
        frames.append(pd.read_sql("SELECT code, main_board, detail FROM fund_board_map", conn))
        conn.close()
    except Exception:
        pass
    if FUND_BOARD_MAP_FILE.exists():
        try:
            frames.append(pd.read_json(FUND_BOARD_MAP_FILE))
        except Exception:
            pass
    out = {}
    for mp in frames:
        if mp is None or len(mp) == 0:
            continue
        for _, r in mp.iterrows():
            code = clean_stock_code(r.get("code", ""))
            if not code:
                continue
            candidate = {
                "main_board": str(r.get("main_board", "") or ""),
                "detail": str(r.get("detail", "") or ""),
            }
            existing = out.get(code)
            candidate_valid = valid_board_name(normalize_board_name(candidate["main_board"]))
            existing_valid = bool(existing and valid_board_name(normalize_board_name(existing.get("main_board"))))
            if existing is None or candidate_valid or not existing_valid:
                out[code] = candidate
    for code, (display_board, temp_board) in FUND_BOARD_OVERRIDES.items():
        existing = out.get(code, {})
        if not valid_board_name(normalize_board_name(existing.get("main_board"))):
            out[code] = {
                "main_board": temp_board,
                "detail": existing.get("detail", f"{display_board}：境内A股基金，使用本地板块兜底。"),
            }
    return out


def score_snapshot(df):
    df = df.copy()
    for col in ["涨跌幅", "净流入", "总成交额", "上涨家数", "下跌家数"]:
        if col in df.columns:
            df[col] = df[col].map(to_num)
        else:
            df[col] = pd.NA
    denom = (df["上涨家数"] + df["下跌家数"]).replace(0, pd.NA)
    df["上涨家数占比"] = df["上涨家数"] / denom

    def rank_score(series, max_score):
        return (series.rank(pct=True) * max_score).round(1)

    df["涨跌幅得分"] = rank_score(df["涨跌幅"], 25)
    df["净流入得分"] = rank_score(df["净流入"], 25)
    df["上涨家数占比得分"] = rank_score(df["上涨家数占比"], 20)
    df["成交额得分"] = rank_score(df["总成交额"], 15)
    df["基础温度分"] = df[["涨跌幅得分", "净流入得分", "上涨家数占比得分", "成交额得分"]].sum(axis=1, min_count=2)
    return df


def score_boards(df, history=None):
    df = score_snapshot(df)
    prev_map = {}
    current_date = ""
    if "snapshot_date" in df.columns and df["snapshot_date"].notna().any():
        current_date = str(df["snapshot_date"].dropna().max())

    if history is not None and len(history) and "snapshot_date" in history.columns:
        dates = sorted([str(x) for x in history["snapshot_date"].dropna().unique()])
        prev_dates = [d for d in dates if d != current_date]
        if prev_dates:
            prev_date = prev_dates[-1]
            prev = score_snapshot(history[history["snapshot_date"].astype(str) == prev_date])
            prev_map = dict(zip(prev["板块"], prev["基础温度分"]))

    df["上一期基础温度分"] = df["板块"].map(prev_map)
    df["温度变化"] = df["基础温度分"] - df["上一期基础温度分"]
    df["历史趋势得分"] = (7.5 + df["温度变化"].fillna(0) * 0.5).clip(0, 15).round(1)
    df["情绪温度分"] = (df["基础温度分"] + df["历史趋势得分"]).round(0)

    score_cols = ["涨跌幅得分", "净流入得分", "上涨家数占比得分", "成交额得分"]
    df["可用字段数"] = df[score_cols].notna().sum(axis=1)
    df["数据可信度"] = df.apply(credibility_label, axis=1)
    df["趋势箭头"] = df["温度变化"].map(trend_label)
    df["情绪标签"] = df.apply(emotion_label, axis=1)
    return df


def credibility_label(row):
    usable = int(row.get("可用字段数", 0) or 0)
    has_history = pd.notna(row.get("上一期基础温度分"))
    if usable >= 4 and has_history:
        return "高"
    if usable >= 4:
        return "中"
    if usable >= 2:
        return "低"
    return "数据不足"


def trend_label(delta):
    if pd.isna(delta):
        return "历史不足"
    if delta >= 5:
        return "↑ 升温"
    if delta <= -5:
        return "↓ 降温"
    return "→ 平稳"


def emotion_label(row):
    cred = row.get("数据可信度", "数据不足")
    score = row.get("情绪温度分")
    delta = row.get("温度变化")
    if cred == "数据不足" or pd.isna(score):
        return "数据不足"
    delta = 0 if pd.isna(delta) else float(delta)
    score = float(score)
    if score <= 35 and delta <= 3:
        return "当日低温"
    if score <= 58 and delta >= 5:
        return "低温升温"
    if score >= 80 and delta <= -5:
        return "高位降温"
    if score >= 82:
        return "高位过热"
    if score >= 65 and delta >= -3:
        return "强势延续"
    return "中性观察"


def recent_sentiment_label(avg_score, delta):
    if pd.isna(avg_score):
        return "历史不足"
    delta = 0 if pd.isna(delta) else float(delta)
    avg_score = float(avg_score)
    if avg_score >= 75 and delta <= -5:
        return "高位降温"
    if avg_score >= 75:
        return "近期拥挤偏热"
    if avg_score >= 60 and delta >= 5:
        return "持续升温"
    if avg_score >= 60:
        return "中高温观察"
    if avg_score <= 35 and delta >= 5:
        return "低温回暖"
    if avg_score <= 35:
        return "长期低温"
    return "中性观察"


def build_recent_sentiment(history, min_days=3, window=5):
    if history is None or len(history) == 0 or "snapshot_date" not in history.columns:
        return {}, "历史不足：还没有本地板块快照。"
    dates = sorted([str(x) for x in history["snapshot_date"].dropna().unique()])
    if len(dates) < min_days:
        return {}, f"历史不足：当前只有 {len(dates)} 个交易日快照，至少需要 {min_days} 个交易日。"

    scored = []
    for d in dates[-window:]:
        one = score_snapshot(history[history["snapshot_date"].astype(str) == d]).copy()
        one["snapshot_date"] = d
        scored.append(one[["板块", "snapshot_date", "基础温度分"]])
    all_scores = pd.concat(scored, ignore_index=True)
    out = {}
    for board, g in all_scores.groupby("板块"):
        g = g.sort_values("snapshot_date")
        avg_score = float(g["基础温度分"].mean())
        delta = float(g["基础温度分"].iloc[-1] - g["基础温度分"].iloc[0]) if len(g) >= 2 else 0.0
        out[str(board)] = {
            "近期温度": avg_score,
            "近期变化": delta,
            "近期情绪": recent_sentiment_label(avg_score, delta),
            "样本天数": len(g),
        }
    return out, f"基于最近 {min(window, len(dates))} 个交易日板块快照。"


def resolve_holding_board(r, fund_map):
    code = clean_stock_code(r["code"])
    base_tag, base_board = BOARD_MAP.get(code, (r["name"], "None"))
    note = ""
    override = fund_board_override(code)
    if override:
        display_board, temp_board = override
        fm = fund_map.get(code, {})
        detail = str(fm.get("detail", "") or "").strip()
        note = f"{r['name']}：境内A股基金，显示为{display_board}。" + (detail if detail else "")
        return display_board, temp_board, note
    if r["type"] in ("otc", "lof") and code in fund_map:
        fm = fund_map[code]
        mb = normalize_board_name(fm["main_board"])
        detail = fm["detail"].strip()
        if valid_board_name(mb):
            note = f"{r['name']}：基金穿透主导板块={mb}。{detail}"
            return mb, mb, note
        if fund_is_foreign_or_non_a(code, fund_map):
            note = f"{r['name']}：基金穿透显示为境外/非A股。{detail}"
            if valid_board_name(base_board):
                return base_tag, base_board, note
            return base_tag, "None", note
        note = f"{r['name']}：境内基金暂未取得足够A股穿透数据。{detail}"
        if valid_board_name(base_board):
            return base_tag, base_board, note
        return base_tag, MISSING_FUND_BOARD, note
    return base_tag, base_board, note


def fund_is_foreign_or_non_a(code, fund_map):
    code = clean_stock_code(code)
    if code in FUND_BOARD_OVERRIDES:
        return False
    kind, _ = classify_fund(code, "otc")
    if kind == "场外基金-QDII/境外":
        return True
    fm = fund_map.get(code, {})
    board = normalize_board_name(fm.get("main_board") or BOARD_MAP.get(code, ["", "None"])[1])
    if str(board).startswith("无（境外/非A股"):
        return True
    return str(board).startswith("无") and kind == "场外基金-QDII/境外"


@st.cache_data(ttl=300)
def cached_get_nav(code, holding_type, name, cache_key):
    return get_nav(code, holding_type, name, cache_key=cache_key)


@st.cache_data(ttl=600)
def compute(cache_key=None):
    _ = cache_key
    fund_map_local = load_fund_board_map()
    rows = []
    for h in HOLDINGS:
        r = dict(h)
        r["share_class"] = str(h.get("share_class", "") or "")
        r["unit_cost"] = float(unit_cost_of(h) or 0)
        try:
            nav_result = cached_get_nav(h["code"], h.get("type", ""), h.get("name", ""), market_cache_key())
            if h["type"] == "otc":
                shares = float(h.get("shares", 0) or 0)
                cost_price = float(unit_cost_of(h) or 0)
                nav = nav_result.nav
                nav_date = nav_result.date
                is_foreign = nav_result.classify == "场外基金-QDII/境外"
                r["现价"] = nav
                r["数据日期"] = nav_date or "暂无数据"
                if nav_result.nav is None:
                    r["净值状态"] = f"暂无数据（接口失败）：{nav_result.reason}"
                elif nav_result.cache:
                    r["净值状态"] = nav_result.reason
                else:
                    suffix = "估值" if nav_result.kind == "估" else "真净值"
                    r["净值状态"] = f"{nav_result.source}：{suffix}，披露/估值日 {nav_result.date}"
                if shares > 0 and cost_price > 0:
                    r["成本额"] = cost_price * shares
                    if nav is not None and pd.notna(nav):
                        r["市值"] = nav * shares
                        r["盈亏"] = (nav - cost_price) * shares
                    else:
                        r["市值"] = float("nan")
                        r["盈亏"] = float("nan")
                else:
                    fallback_mv = h.get("market_value")
                    fallback_profit = h.get("profit")
                    if fallback_mv not in (None, ""):
                        r["市值"] = float(fallback_mv)
                        r["盈亏"] = float(fallback_profit or 0)
                        r["成本额"] = r["市值"] - r["盈亏"]
                        r["净值状态"] = f"{r['净值状态']}；这只仍是旧持仓格式，请在持仓管理改为“份额+成本单价”后才能自动重算市值"
                    else:
                        r["市值"] = float("nan")
                        r["盈亏"] = float("nan")
                        r["成本额"] = float("nan")
                        r["净值状态"] = "请在持仓管理填写份额和成本单价"
                day_amount, day_pct, day_status = calc_daily_return(nav_result, shares)
                r["当日收益率"] = day_pct if day_pct is not None else float("nan")
                r["今日估算盈亏"] = day_amount if day_amount is not None else float("nan")
                if day_status == "待披露":
                    r["当日收益说明"] = "待披露"
                elif day_status == "份额缺失":
                    r["当日收益说明"] = "份额缺失"
                elif day_status in ("估", "真"):
                    r["当日收益说明"] = "盘中估值" if day_status == "估" else "已公布真净值"
                elif is_foreign:
                    r["当日收益说明"] = "QDII/境外净值披露滞后"
                else:
                    r["当日收益说明"] = day_status or "暂无当日估值"
            else:
                p = nav_result.nav
                if p is None or pd.isna(p):
                    raise RuntimeError(nav_result.reason or "行情暂不可用")
                r["现价"] = p
                r["市值"] = p * float(h["shares"])
                r["成本额"] = float(unit_cost_of(h) or 0) * h["shares"]
                r["盈亏"] = r["市值"] - r["成本额"]
                day_amount, day_pct, day_status = calc_daily_return(nav_result, h.get("shares", 0))
                r["当日收益率"] = day_pct if day_pct is not None else float("nan")
                r["今日估算盈亏"] = day_amount if day_amount is not None else float("nan")
                r["当日收益说明"] = "新浪行情"
                r["数据日期"] = nav_result.date
                r["净值状态"] = nav_result.reason
            r["盈亏率"] = r["盈亏"] / r["成本额"] * 100 if pd.notna(r["成本额"]) and r["成本额"] else float("nan")
        except Exception as e:
            r["现价"] = float("nan")
            r["市值"] = float("nan") if h.get("type") == "otc" else 0
            r["盈亏"] = float("nan") if h.get("type") == "otc" else 0
            r["成本额"] = float("nan") if h.get("type") == "otc" else 0
            r["盈亏率"] = float("nan") if h.get("type") == "otc" else 0
            r["今日估算盈亏"] = 0.0
            r["当日收益率"] = float("nan")
            r["当日收益说明"] = "数据暂不可用"
            r["数据日期"] = ""
            r["净值状态"] = f"净值暂未获取：{str(e)[:60]}" if h.get("type") == "otc" else ""
            r["错误"] = str(e)[:80]
        rows.append(r)
    cols = [
        "account", "type", "name", "code", "share_class", "unit_cost", "cost", "shares", "market_value", "profit",
        "buy_date", "现价", "市值", "盈亏", "成本额", "盈亏率", "今日估算盈亏", "当日收益率", "当日收益说明", "数据日期", "净值状态", "错误",
    ]
    return pd.DataFrame(rows, columns=cols)


def build_exposure(df, fund_map, live, recent_sentiment=None):
    recent_sentiment = recent_sentiment or {}
    total_mv = df["市值"].sum()
    agg = {}
    for _, r in df.iterrows():
        tag, temp_board, note = resolve_holding_board(r, fund_map)
        if tag not in agg:
            agg[tag] = {"板块": tag, "温度板块": temp_board, "持仓市值": 0.0, "明细": [], "说明": []}
        mv = float(r["市值"]) if pd.notna(r["市值"]) else 0.0
        agg[tag]["持仓市值"] += mv
        agg[tag]["明细"].append(r["name"])
        if note:
            agg[tag]["说明"].append(note)

    rows = []
    for info in agg.values():
        tb = info["温度板块"]
        row = live[live["板块"] == tb] if live is not None and tb != "None" else pd.DataFrame()
        score = float(row.iloc[0]["情绪温度分"]) if len(row) else float("nan")
        if len(row):
            label = str(row.iloc[0]["情绪标签"])
            cred = str(row.iloc[0]["数据可信度"])
        elif tb == "None":
            label = "境外/非A股"
            cred = "不适用"
        elif tb == MISSING_FUND_BOARD:
            label = "穿透暂缺"
            cred = "数据不足"
        else:
            label = "数据不足"
            cred = "数据不足"
        trend = str(row.iloc[0]["趋势箭头"]) if len(row) else "—"
        recent = recent_sentiment.get(tb, {})
        recent_fallback = "境外/非A股" if tb == "None" else ("穿透暂缺" if tb == MISSING_FUND_BOARD else "历史不足")
        rows.append({
            "板块": info["板块"],
            "温度板块": tb,
            "持仓市值": info["持仓市值"],
            "占总资产比例": info["持仓市值"] / total_mv * 100 if total_mv else 0,
            "温度分": score,
            "情绪标签": label,
            "数据可信度": cred,
            "趋势": trend,
            "近期温度": recent.get("近期温度", float("nan")),
            "近期变化": recent.get("近期变化", float("nan")),
            "近期情绪": recent.get("近期情绪", recent_fallback),
            "近期样本": recent.get("样本天数", 0),
            "明细": "、".join(info["明细"]),
            "说明": "；".join(info["说明"]),
        })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values("持仓市值", ascending=False).reset_index(drop=True)
    return out


def kline(df):
    d = df.tail(120).copy()
    x = list(range(len(d)))
    step = max(1, len(d) // 8)
    ti = list(range(0, len(d), step))
    tt = [d["date"].dt.strftime("%y-%m-%d").iloc[i] for i in ti]
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        row_heights=[.6, .2, .2],
        vertical_spacing=.05,
        subplot_titles=("K线 + MA", "MACD", "RSI"),
    )
    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"], close=d["close"], name="K线"), 1, 1)
    for ma in ["MA5", "MA10", "MA20"]:
        fig.add_trace(go.Scatter(x=x, y=d[ma], name=ma, mode="lines"), 1, 1)
    fig.add_trace(go.Bar(x=x, y=d["MACD动能"], name="MACD动能"), 2, 1)
    fig.add_trace(go.Scatter(x=x, y=d["DIF"], name="DIF"), 2, 1)
    fig.add_trace(go.Scatter(x=x, y=d["DEA"], name="DEA"), 2, 1)
    fig.add_trace(go.Scatter(x=x, y=d["RSI"], name="RSI"), 3, 1)
    fig.add_hline(y=70, line_dash="dot", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", row=3, col=1)
    fig.update_xaxes(tickmode="array", tickvals=ti, ticktext=tt, rangeslider_visible=False)
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=45, b=10), legend=dict(orientation="h"))
    return fig


def score_breakdown(row):
    return pd.DataFrame([
        {"维度": "涨跌幅", "得分": f"{row.get('涨跌幅得分', 0):.1f}/25"},
        {"维度": "净流入", "得分": f"{row.get('净流入得分', 0):.1f}/25"},
        {"维度": "上涨家数占比", "得分": f"{row.get('上涨家数占比得分', 0):.1f}/20"},
        {"维度": "成交额", "得分": f"{row.get('成交额得分', 0):.1f}/15"},
        {"维度": "历史趋势", "得分": f"{row.get('历史趋势得分', 0):.1f}/15"},
        {"维度": "总分", "得分": f"{row.get('情绪温度分', 0):.0f}/100"},
    ])


def board_trend_table(rows):
    if rows is None or len(rows) == 0:
        return []
    return rows.to_dict("records")


def extract_weight_text(detail):
    m = re.search(r"A股匹配权重\s*([0-9.]+%)", str(detail or ""))
    return m.group(1) if m else "—"


def health_summary(df, board_source, using_old):
    log = load_update_log()
    latest_log_time = log["update_time"].max() if len(log) and "update_time" in log.columns else ""
    try:
        conn = sqlite3.connect(DB)
        board_date = conn.execute("SELECT MAX(snapshot_date) FROM board_heat").fetchone()[0] or ""
        conn.close()
    except Exception:
        board_date = ""
    stock_dates = df[df["type"] == "stock"]["数据日期"].dropna().astype(str)
    lof_dates = df[df["type"] == "lof"]["数据日期"].dropna().astype(str)
    otc_dates = df[df["type"] == "otc"]["数据日期"].dropna().astype(str)
    today_logs = log[log["update_time"].astype(str).str.startswith(datetime.now().strftime("%Y-%m-%d"))] if len(log) else pd.DataFrame()
    failed = today_logs[today_logs["status"] != "成功"] if len(today_logs) else pd.DataFrame()
    success = today_logs[today_logs["status"] == "成功"] if len(today_logs) else pd.DataFrame()
    if len(today_logs) == 0:
        update_status = "无今日日志"
    elif len(failed) == 0:
        update_status = "成功"
    elif len(success):
        update_status = "部分成功"
    else:
        update_status = "失败"

    return pd.DataFrame([
        {"项目": "今日是否交易日", "内容": "是（未校验节假日）" if datetime.now().weekday() < 5 else "否（周末）"},
        {"项目": "最新更新时间", "内容": latest_log_time or f"页面刷新 {datetime.now():%Y-%m-%d %H:%M:%S}"},
        {"项目": "板块情绪数据日期", "内容": board_date or "无"},
        {"项目": "A股行情数据日期", "内容": stock_dates.max() if len(stock_dates) else "无"},
        {"项目": "场内基金行情数据日期", "内容": lof_dates.max() if len(lof_dates) else "无"},
        {"项目": "场外基金净值日期", "内容": otc_dates.max() if len(otc_dates) else "无"},
        {"项目": "update_data.py 是否成功", "内容": update_status},
        {"项目": "失败接口", "内容": "、".join(failed["task_name"].astype(str).unique()) if len(failed) else "无"},
        {"项目": "重试次数", "内容": str(int(today_logs["retry_count"].fillna(0).sum())) if len(today_logs) and "retry_count" in today_logs.columns else "0"},
        {"项目": "错误原因", "内容": "；".join(failed["error_msg"].dropna().astype(str).head(3)) if len(failed) else "无"},
        {"项目": "板块数据来源", "内容": board_source},
        {"项目": "当前页面是否使用旧数据", "内容": "是" if using_old else "否"},
    ])


def daily_recap(exposure, total_mv):
    if exposure is None or len(exposure) == 0:
        return "当前没有足够数据生成复盘。以上内容仅为客观数据描述，不构成买卖建议。"
    top = exposure.iloc[0]
    hot = exposure.dropna(subset=["近期温度"]).sort_values("近期温度", ascending=False).head(1)
    lines = [f"你的持仓主要集中在 {top['板块']}，占总资产约 {top['占总资产比例']:.1f}%。"]
    if len(hot):
        h = hot.iloc[0]
        lines.append(f"{h['板块']} 近期温度约 {h['近期温度']:.0f}，状态为“{h['近期情绪']}”。")
        if h["占总资产比例"] >= 20 and h["近期温度"] >= 75:
            lines.append(f"{h['板块']} 属于“高仓位 + 近期高热度”组合，需要关注波动和回撤风险。")
    foreign = exposure[exposure["情绪标签"] == "境外/非A股"]
    if len(foreign):
        lines.append("部分港美或全球基金无法直接用 A 股板块温度衡量，应单独观察其市场和汇率风险。")
    lines.append(RISK_TIP)
    return "\n\n".join(lines)


def holding_card(r, with_chart=False, otc=False):
    pnl = float(r.get("盈亏", float("nan")))
    pnl_rate = float(r.get("盈亏率", float("nan")))
    nav_text = "净值暂未获取" if otc and pd.isna(r.get("现价")) else fmt_price(r.get("现价"))
    pnl_text = "—" if pd.isna(pnl) else f"{pnl:+,.0f} 元"
    pnl_rate_text = "—" if pd.isna(pnl_rate) else f"{pnl_rate:+.1f}%"
    st.markdown(
        f"""
        <div class="holding-card">
            <div class="holding-title">{esc(r.get('name', ''))}</div>
            <div class="holding-meta">{esc(r.get('code', ''))} ｜ {esc(r.get('数据日期', '') or '净值/行情日期待更新')}</div>
            <div class="kv-grid">
                <div class="kv"><div class="kv-label">现价/净值</div><div class="kv-value">{esc(nav_text)}</div></div>
                <div class="kv"><div class="kv-label">市值</div><div class="kv-value">{fmt_money(r.get('市值'))}</div></div>
                <div class="kv"><div class="kv-label">盈亏</div><div class="kv-value {cls(pnl)}">{pnl_text}</div></div>
                <div class="kv"><div class="kv-label">盈亏率</div><div class="kv-value {cls(pnl_rate)}">{pnl_rate_text}</div></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if otc and r.get("净值状态") and r.get("净值状态") != "已按最新单位净值自动计算":
        st.caption(str(r.get("净值状态")))
    if otc:
        nav = otc_nav(r["code"])
        if nav is not None and len(nav):
            nav = nav.copy()
            nav.columns = ["日期", "单位净值", "日增长率"][:len(nav.columns)]
            nav["日期"] = pd.to_datetime(nav["日期"])
            nav = nav.tail(250)
            fig = go.Figure(go.Scatter(x=nav["日期"], y=pd.to_numeric(nav["单位净值"], errors="coerce"), line=dict(color="#2563eb")))
            fig.update_layout(height=260, title="单位净值走势", margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)
    elif with_chart:
        with st.expander("K线 / MA / MACD / RSI", expanded=False):
            try:
                st.plotly_chart(kline(price_hist(r["code"], r["type"] == "lof")), use_container_width=True)
            except Exception as e:
                st.error(f"K线拉取失败：{e}")


def aggregate_holdings_for_display(df):
    if df is None or len(df) == 0:
        return pd.DataFrame()
    rows = []
    group_cols = ["account", "type", "code", "share_class"] if "share_class" in df.columns else ["account", "type", "code"]
    for group_key, g in df.groupby(group_cols, dropna=False):
        if len(group_cols) == 4:
            account, htype, code, share_class = group_key
        else:
            account, htype, code = group_key
            share_class = ""
        first = g.iloc[0].to_dict()
        shares = pd.to_numeric(g.get("shares"), errors="coerce").fillna(0).sum()
        cost_source = g["unit_cost"] if "unit_cost" in g.columns else g["cost"]
        cost_series = pd.to_numeric(cost_source, errors="coerce").fillna(0)
        cost_amount = (cost_series * pd.to_numeric(g.get("shares"), errors="coerce").fillna(0)).sum()
        mv = pd.to_numeric(g["市值"], errors="coerce").sum()
        cost_total = pd.to_numeric(g["成本额"], errors="coerce").sum()
        pnl = pd.to_numeric(g["盈亏"], errors="coerce").sum()
        today_pnl = pd.to_numeric(g["今日估算盈亏"], errors="coerce").sum(min_count=1)
        row = dict(first)
        row["account"] = account
        row["type"] = htype
        row["code"] = str(code)
        row["share_class"] = str(share_class or "")
        row["shares"] = shares
        row["unit_cost"] = cost_amount / shares if shares else first.get("unit_cost", first.get("cost", float("nan")))
        row["cost"] = row["unit_cost"]
        row["市值"] = mv
        row["成本额"] = cost_total if cost_total else cost_amount
        row["盈亏"] = pnl
        row["盈亏率"] = pnl / row["成本额"] * 100 if row["成本额"] else float("nan")
        row["今日估算盈亏"] = today_pnl
        row["当日收益率"] = today_pnl / mv * 100 if pd.notna(today_pnl) and mv else float("nan")
        row["数据日期"] = str(g["数据日期"].dropna().astype(str).max()) if "数据日期" in g and len(g["数据日期"].dropna()) else ""
        row["buy_date"] = str(g["buy_date"].dropna().astype(str).min()) if "buy_date" in g and len(g["buy_date"].dropna()) else ""
        row["detail_key"] = detail_key_for_row(row)
        rows.append(row)
    return pd.DataFrame(rows)


def enrich_holding_estimates(show, fund_map):
    if show is None or len(show) == 0:
        return show
    out = show.copy()
    for idx, r in out.iterrows():
        if r.get("type") != "otc" or pd.notna(r.get("今日估算盈亏")):
            continue
        if float(r.get("shares", 0) or 0) <= 0:
            out.at[idx, "当日收益说明"] = "份额缺失"
            continue
        if fund_is_foreign_or_non_a(r.get("code"), fund_map):
            out.at[idx, "当日收益说明"] = "待披露"
            continue
        out.at[idx, "当日收益说明"] = "待披露"
    return out


def board_display_for_row(r, fund_map, live):
    code = clean_stock_code(r.get("code"))
    tag, temp_board, _ = resolve_holding_board(r, fund_map)
    override = fund_board_override(code)
    if override:
        display_board, temp_board = override
        detail = fund_map.get(code, {}).get("detail", "")
        if display_board == "有色金属":
            return display_board, weighted_board_change_from_detail(detail, live, METAL_BOARDS)
        if display_board == "A股量化":
            return display_board, weighted_board_change_from_detail(detail, live)
    board = temp_board if valid_board_name(temp_board) else tag
    if not valid_board_name(temp_board):
        return ("境外/非A股" if fund_is_foreign_or_non_a(code, fund_map) else "暂无估值"), float("nan")
    if live is None or len(live) == 0 or "板块" not in live.columns:
        return board, float("nan")
    m = live[live["板块"] == temp_board]
    if len(m) == 0:
        return board, float("nan")
    change = to_num(m.iloc[0].get("涨跌幅", float("nan")))
    return board, change


def render_value_block(label, value, sub="", tone="flat"):
    label_html = f'<div class="holding-list-label">{esc(label)}</div>' if label else ""
    st.markdown(
        f"""
        {label_html}
        <div class="holding-list-value {tone}">{esc(value)}</div>
        <div class="holding-list-sub">{esc(sub)}</div>
        """,
        unsafe_allow_html=True,
    )


def holding_update_meta(date_text):
    s = str(date_text or "").strip()
    if not s or s in ("nan", "NaT", "None"):
        return "—"
    try:
        d = pd.to_datetime(s, errors="coerce")
        if pd.isna(d):
            return s
        today = china_today_date()
        short = d.strftime("%m-%d")
        return f"{short} 已更新" if d.normalize() == today else short
    except Exception:
        return s


def weighted_board_change_from_detail(detail, live, allowed_boards=None):
    if live is None or len(live) == 0 or "板块" not in live.columns or not detail:
        return float("nan")
    total = 0.0
    weighted = 0.0
    for board, weight_text in re.findall(r"\(([^,()]+),\s*([0-9.]+)%\)", str(detail)):
        board = normalize_board_name(board)
        if allowed_boards and board not in allowed_boards:
            continue
        match = live[live["板块"] == board]
        if len(match) == 0:
            continue
        change = to_num(match.iloc[0].get("涨跌幅", float("nan")))
        if pd.isna(change):
            continue
        weight = float(weight_text)
        weighted += change * weight
        total += weight
    return weighted / total if total else float("nan")


def render_holdings_list(df, snapshot_history, fund_map, live):
    show = enrich_holding_estimates(aggregate_holdings_for_display(df), fund_map)
    if len(show) == 0:
        st.info("暂无持仓。")
        risk_notice(st)
        return

    total_mv = pd.to_numeric(show["市值"], errors="coerce").sum()
    total_today = pd.to_numeric(show["今日估算盈亏"], errors="coerce").sum(min_count=1)
    st.markdown(
        f"""
        <div class="holding-topbar">
            <div class="holding-topitem">
                <div class="holding-toplabel">三账户总市值</div>
                <div class="holding-topvalue">{esc(fmt_money(total_mv))}</div>
            </div>
            <div class="holding-topitem">
                <div class="holding-toplabel">三账户当日收益</div>
                <div class="holding-topvalue {cls(total_today)}">{esc(signed_money(total_today))}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    current_sort = st.session_state.get("holding_sort_by", "今日估算盈亏")
    current_desc = st.session_state.get("holding_sort_desc", True)

    def sort_href(sort_key):
        next_desc = (not current_desc) if current_sort == sort_key else True
        short = "today" if sort_key == "今日估算盈亏" else "profit"
        return f"?page=holding&hold_sort={short}&hold_desc={'1' if next_desc else '0'}"

    today_mark = " ↓" if current_sort == "今日估算盈亏" and current_desc else (" ↑" if current_sort == "今日估算盈亏" else "")
    profit_mark = " ↓" if current_sort == "盈亏" and current_desc else (" ↑" if current_sort == "盈亏" else "")
    st.markdown(
        f"""
        <div class="holding-list-head">
            <div>名称 / 市值 / 日期</div>
            <a href="{sort_href('今日估算盈亏')}">当日收益{esc(today_mark)}</a>
            <div>关联板块</div>
            <a href="{sort_href('盈亏')}">持有收益{esc(profit_mark)}</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
    sort_by = st.session_state.get("holding_sort_by", "account")
    sort_desc = st.session_state.get("holding_sort_desc", True)
    if sort_by in show.columns:
        show = show.sort_values(["account", sort_by], ascending=[True, not sort_desc], na_position="last")

    account_order = ["银河证券", "东方财富", "支付宝"]
    for account in account_order:
        sub = show[show["account"] == account]
        if len(sub) == 0:
            continue
        st.markdown(f'<div class="account-title">{esc(account)}</div>', unsafe_allow_html=True)
        rows_html = []
        for _, r in sub.iterrows():
            board, board_chg = board_display_for_row(r, fund_map, live)
            day_sub = signed_pct(r.get("当日收益率")) if pd.notna(r.get("今日估算盈亏")) else str(r.get("当日收益说明", "—"))
            board_sub = signed_pct(board_chg) if pd.notna(board_chg) else "—"
            detail_href = (
                f"?page=holding&detail_code={quote_plus(str(r.get('code', '')))}"
                f"&detail_key={quote_plus(str(r.get('detail_key', '')))}"
            )
            rows_html.append(
                f"""
                <div class="holding-list-row">
                    <div>
                        <a class="holding-name-link" href="{detail_href}">{esc(r.get("name", ""))}</a>
                        <div class="holding-list-meta">￥{float(r.get("市值", 0) or 0):,.0f}&nbsp;&nbsp;{esc(holding_update_meta(r.get("数据日期")))}</div>
                    </div>
                    <div class="holding-cell">
                        <div class="holding-list-value {cls(r.get("今日估算盈亏"))}">{esc(signed_money(r.get("今日估算盈亏")))}</div>
                        <div class="holding-list-sub">{esc(day_sub)}</div>
                    </div>
                    <div class="holding-cell">
                        <div class="holding-list-value {cls(board_chg)}">{esc(str(board))}</div>
                        <div class="holding-list-sub">{esc(board_sub)}</div>
                    </div>
                    <div class="holding-cell">
                        <div class="holding-list-value {cls(r.get("盈亏"))}">{esc(signed_money(r.get("盈亏")))}</div>
                        <div class="holding-list-sub">{esc(signed_pct(r.get("盈亏率")))}</div>
                    </div>
                </div>
                """
            )
        st.markdown("".join(rows_html), unsafe_allow_html=True)
        sub_mv = pd.to_numeric(sub["市值"], errors="coerce").sum()
        sub_today = pd.to_numeric(sub["今日估算盈亏"], errors="coerce").sum(min_count=1)
        sub_profit = pd.to_numeric(sub["盈亏"], errors="coerce").sum()
        st.markdown(
            f'<div class="holding-subtotal">{esc(account)} 小计：市值 {esc(fmt_money(sub_mv))} ｜ 当日 {esc(signed_money(sub_today))} ｜ 持有收益 {esc(signed_money(sub_profit))}</div>',
            unsafe_allow_html=True,
        )
    risk_notice(st)


def filter_period(df, date_col, period):
    if df is None or len(df) == 0 or period == "成立来":
        return df
    days = {"1月": 31, "3月": 93, "6月": 186, "1年": 366}.get(period)
    if not days:
        return df
    end = pd.to_datetime(df[date_col]).max()
    return df[pd.to_datetime(df[date_col]) >= end - pd.Timedelta(days=days)]


def render_performance_tab(r):
    period = st.segmented_control("区间", ["1月", "3月", "6月", "1年", "成立来"], default="3月", key=f"perf_period_{r['detail_key']}")
    try:
        if r["type"] == "otc":
            nav = otc_nav_history_frame(r["code"])
            if len(nav) == 0:
                st.info("暂无净值历史。")
                return
            nav = filter_period(nav, "日期", period)
            fig = go.Figure(go.Scatter(x=nav["日期"], y=nav["单位净值"], name="单位净值", mode="lines"))
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            hist = price_hist(r["code"], r["type"] == "lof")
            hist = filter_period(hist, "date", period)
            fig = go.Figure(go.Scatter(x=hist["date"], y=hist["close"], name="收盘价/净值", mode="lines"))
            fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"走势拉取失败：{e}")


def render_my_return_tab(r, snapshot_history):
    hid = holding_snapshot_id(r["account"], r["type"], r["code"], r.get("share_class", ""))
    profit_col = f"holding_{hid}_profit"
    mv_col = f"holding_{hid}_mv"
    ret_col = f"holding_{hid}_return_pct"
    if snapshot_history is None or len(snapshot_history) < 2 or profit_col not in snapshot_history.columns:
        st.info("个人收益曲线从本次改造后的每日快照开始积累，历史不补。")
        return
    dfh = snapshot_history.dropna(subset=["date"]).copy()
    dfh = dfh.dropna(subset=[profit_col])
    if len(dfh) < 2:
        st.info("数据积累中，明日起逐步生成这只持仓的个人收益曲线。")
        return
    st.caption(f"数据自 {pd.to_datetime(dfh['date']).min().date()} 起记录，历史无法补全。")
    mode = st.segmented_control("曲线类型", ["收益金额", "收益率", "市值"], default="收益金额", key=f"holding_curve_{hid}")
    col = {"收益金额": profit_col, "收益率": ret_col, "市值": mv_col}[mode]
    fig = go.Figure(go.Scatter(x=dfh["date"], y=pd.to_numeric(dfh[col], errors="coerce"), mode="lines+markers", name=mode))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)


def render_related_board_tab(r, fund_map, live, board_history):
    board, board_chg = board_display_for_row(r, fund_map, live)
    if board == "境外/非A股":
        st.info("境外/非A股资产暂无 A 股盘中估值，需单独观察其市场、汇率和基金净值公布节奏。")
        return
    c1, c2 = st.columns(2)
    with c1:
        card("关联板块", esc(board), "")
    with c2:
        card("板块涨跌", f'<span class="{cls(board_chg)}">{signed_pct(board_chg)}</span>', "")
    if r["type"] == "otc":
        est_pct, detail, note = fund_top_a_share_estimate(r["code"])
        if pd.notna(est_pct):
            st.metric("前十大 A 股重仓估算", signed_pct(est_pct), help="按前十大 A股重仓权重和对应A股涨跌做静态估算，不代表真实净值。")
        st.caption(note)
        if len(detail):
            st.dataframe(detail, use_container_width=True, hide_index=True)
    if board_history is not None and len(board_history) and "板块" in board_history.columns:
        hist = board_history[board_history["板块"] == board].copy()
        if len(hist):
            hist["snapshot_date"] = pd.to_datetime(hist["snapshot_date"].astype(str), format="%Y%m%d", errors="coerce")
            hist["涨跌幅"] = pd.to_numeric(hist["涨跌幅"], errors="coerce")
            fig = go.Figure(go.Scatter(x=hist["snapshot_date"], y=hist["涨跌幅"], mode="lines+markers", name=f"{board}涨跌幅"))
            fig.update_layout(height=240, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)


def render_holding_detail(df, snapshot_history, fund_map, live, board_history):
    show = enrich_holding_estimates(aggregate_holdings_for_display(df), fund_map)
    key = st.session_state.get("detail_key")
    code = st.session_state.get("detail_code")
    if key and "detail_key" in show.columns:
        match = show[show["detail_key"] == key]
    else:
        match = show[show["code"].astype(str) == str(code)]
    if len(match) == 0:
        st.session_state.pop("detail_code", None)
        st.session_state.pop("detail_key", None)
        st.session_state["main_page"] = "持仓"
        st.warning("这只持仓暂时找不到，已返回列表。")
        st.rerun()
    r = match.iloc[0]
    if st.button("← 返回", key="back_to_holding_list"):
        st.session_state.pop("detail_code", None)
        st.session_state.pop("detail_key", None)
        st.session_state["main_page"] = "持仓"
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()
    st.subheader(f"{r['name']}（{r['code']}）")
    total_mv = pd.to_numeric(show["市值"], errors="coerce").sum()
    position_pct = float(r["市值"]) / total_mv * 100 if total_mv and pd.notna(r["市值"]) else float("nan")
    buy_date = str(r.get("buy_date", "") or "").strip()
    holding_days = "—"
    if buy_date:
        dt = pd.to_datetime(buy_date, errors="coerce")
        if pd.notna(dt):
            holding_days = f"{max((pd.Timestamp.today().normalize() - dt.normalize()).days, 0)} 天"
    hid = holding_snapshot_id(r["account"], r["type"], r["code"], r.get("share_class", ""))
    yesterday_profit = "—"
    try:
        profit_col = f"holding_{hid}_profit"
        if snapshot_history is not None and profit_col in snapshot_history.columns:
            hist = snapshot_history.dropna(subset=["date"]).sort_values("date")
            vals = pd.to_numeric(hist[profit_col], errors="coerce").dropna()
            if len(vals) >= 2:
                yesterday_profit = signed_money(vals.iloc[-1] - vals.iloc[-2])
    except Exception:
        yesterday_profit = "—"

    c = st.columns(3)
    with c[0]:
        card("金额", esc(fmt_money(r["市值"])), "")
    with c[1]:
        shares_text = "—" if not float(r.get("shares", 0) or 0) else f"{float(r.get('shares', 0) or 0):,.0f}"
        card("份额", shares_text, "")
    with c[2]:
        card("占比", esc(pct_text(position_pct)), "占总资产")
    c2 = st.columns(3)
    with c2[0]:
        card("持有收益", value_html(r["盈亏"], " 元", signed=True), "", cls(r["盈亏"]))
    with c2[1]:
        card("收益率", f'<span class="{cls(r["盈亏率"])}">{esc(signed_pct(r["盈亏率"]))}</span>', "")
    with c2[2]:
        card("持仓成本", esc(fmt_price(r.get("unit_cost", r.get("cost")))), "单位成本")
    c3 = st.columns(3)
    with c3[0]:
        card("当日收益", esc(signed_money(r["今日估算盈亏"])), signed_pct(r["当日收益率"]), cls(r["今日估算盈亏"]))
    with c3[1]:
        card("昨日收益", esc(yesterday_profit), "按每日快照估算")
    with c3[2]:
        card("持有天数", esc(holding_days), "可在持仓管理填写首次买入日期")

    tab1, tab2, tab3 = st.tabs(["业绩走势", "我的收益", "关联板块"])
    with tab1:
        render_performance_tab(r)
    with tab2:
        render_my_return_tab(r, snapshot_history)
    with tab3:
        render_related_board_tab(r, fund_map, live, board_history)
    risk_notice(st)


def holding_import_widget(template_name, key_prefix):
    cfg = IMPORT_TEMPLATES[template_name]
    st.info(cfg["note"])
    st.caption(f"本入口会自动补：{cfg['account']} + {cfg['type']}。粘贴内容只需要 name, code, shares, cost；如果自带 account/type，则按粘贴内容校验。")

    uploaded = st.file_uploader(
        f"上传{template_name}（只预览，不自动写入）",
        type=["png", "jpg", "jpeg"],
        key=f"{key_prefix}_upload",
    )
    if uploaded is not None:
        st.image(uploaded, caption="已上传截图。当前未接视觉 API，请粘贴识别草稿后预览。")

    with st.expander("视觉模型提示词", expanded=False):
        st.code(cfg["prompt"], language="text")

    raw_key = f"{key_prefix}_raw_text"
    proposed_key = f"{key_prefix}_proposed"
    notes_key = f"{key_prefix}_duplicate_notes"
    st.session_state.setdefault(raw_key, "")
    st.session_state.setdefault(proposed_key, None)
    st.session_state.setdefault(notes_key, [])

    if st.button(f"载入{template_name}示例", key=f"{key_prefix}_load_sample"):
        st.session_state[raw_key] = cfg["sample"]
        st.session_state[proposed_key] = None
        st.session_state[notes_key] = []

    raw = st.text_area("粘贴识别结果（JSON 或 CSV）", height=180, key=raw_key)
    if st.button("解析并预览", type="primary", key=f"{key_prefix}_parse"):
        try:
            parsed = parse_records(raw)
            proposed, errors = normalize_records(parsed, default_account=cfg["account"], default_type=cfg["type"])
            proposed, duplicate_notes = consolidate_same_code(proposed)
            wrong_scope = [r for r in proposed if r.get("account") != cfg["account"] or r.get("type") != cfg["type"]]
            if errors:
                st.session_state[proposed_key] = None
                st.session_state[notes_key] = []
                st.error("有记录未通过校验，请先修正。")
                st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
            elif wrong_scope:
                st.session_state[proposed_key] = None
                st.session_state[notes_key] = []
                st.error(f"识别结果混入了非本页账户/类型。本页只允许：{cfg['account']} + {cfg['type']}。")
                st.dataframe(pd.DataFrame(display_records(wrong_scope)), use_container_width=True, hide_index=True)
            elif not proposed:
                st.session_state[proposed_key] = None
                st.session_state[notes_key] = []
                st.warning("没有解析到持仓记录。")
            else:
                st.session_state[proposed_key] = proposed
                st.session_state[notes_key] = duplicate_notes
                st.success(f"已解析 {len(proposed)} 条，请核对后再写入。")
        except Exception as e:
            st.session_state[proposed_key] = None
            st.session_state[notes_key] = []
            st.error(f"解析失败：{e}")

    proposed = st.session_state.get(proposed_key)
    if proposed:
        duplicate_notes = st.session_state.get(notes_key, [])
        current = current_holdings()
        final_records = merge_records(current, proposed, "replace_same_account_type")
        if duplicate_notes:
            st.warning("同一代码多行处理结果如下，请重点核对。")
            st.dataframe(pd.DataFrame(duplicate_notes), use_container_width=True, hide_index=True)
        st.subheader("差异预览")
        render_diff_preview(diff_records(current, final_records))
        st.subheader("识别结果预览")
        st.dataframe(pd.DataFrame(display_records(proposed)), use_container_width=True, hide_index=True)
        checked = st.checkbox("我已核对代码、数量/份额和成本。", key=f"{key_prefix}_checked")
        phrase = st.text_input("请输入：确认写入", key=f"{key_prefix}_phrase")
        if st.button("备份旧文件并写入 holdings_data.json", disabled=not (checked and phrase == "确认写入"), key=f"{key_prefix}_write"):
            try:
                backup, sync_ok, sync_msg = save_managed_holdings(final_records)
                handle_holdings_save_result(backup, sync_ok, sync_msg)
            except Exception as e:
                st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
                st.error(f"写入流程异常：{type(e).__name__}：{e}")
                render_github_sync_diag(expanded=False)


TYPE_LABELS = {
    "stock": "A股个股",
    "lof": "场内基金/LOF",
    "otc": "场外基金",
}
LABEL_TYPES = {v: k for k, v in TYPE_LABELS.items()}


def clean_managed_record(raw):
    item = {
        "account": raw["account"],
        "type": raw["type"],
        "name": str(raw.get("name", "")).strip(),
        "code": str(raw.get("code", "")).strip(),
        "share_class": str(raw.get("share_class", "") or "").strip().upper(),
    }
    if not item["name"]:
        raise ValueError("名称不能为空")
    if not item["code"]:
        raise ValueError("代码不能为空")
    if item["type"] in {"stock", "lof", "otc"}:
        item["unit_cost"] = float(raw.get("unit_cost", raw.get("cost")) or 0)
        item["shares"] = float(raw.get("shares") or 0)
        if item["unit_cost"] <= 0 or item["shares"] <= 0:
            raise ValueError("单位成本和持有份额必须大于 0")
    else:
        item["market_value"] = float(raw.get("market_value") or 0)
        item["profit"] = float(raw.get("profit") or 0)
    if raw.get("buy_date"):
        item["buy_date"] = str(raw.get("buy_date")).strip()
    return item


def save_managed_holdings(records):
    global HOLDINGS, BOARD_MAP
    backup = write_holdings(records, BOARD_MAP)
    json_text = HOLDINGS_DATA_FILE.read_text(encoding="utf-8")
    st.session_state["holdings_data_json"] = json_text
    st.session_state["holdings_records"] = json.loads(json_text).get("holdings", [])
    sync_ok, sync_msg = push_holdings_to_github(json_text)
    HOLDINGS, BOARD_MAP = load_holdings_data()
    st.cache_data.clear()
    return backup, sync_ok, sync_msg


def show_holdings_save_result(backup, sync_ok, sync_msg):
    if sync_ok:
        st.session_state["holdings_save_notice"] = ("success", "已保存并同步到云端，应用将在约1分钟后刷新为最新数据", str(backup or ""))
        st.success("已保存并同步到云端，应用将在约1分钟后刷新为最新数据")
        if backup:
            st.caption(f"旧文件已备份到：{backup}")
    else:
        st.session_state["holdings_save_notice"] = ("error", sync_msg, "")
        st.error(sync_msg)
        render_github_sync_diag(expanded=False)


def handle_holdings_save_result(backup, sync_ok, sync_msg):
    show_holdings_save_result(backup, sync_ok, sync_msg)
    if sync_ok:
        st.rerun()


def _alipay_code_label(code, options):
    if not code:
        return "请选择代码"
    for opt in options:
        if opt["code"] == code:
            return f"{code}｜{opt.get('name', '')}"
    return code


def _render_alipay_preview_table(rows):
    if not rows:
        st.info("还没有可预览的记录。")
        return
    df = pd.DataFrame(rows)

    def _shade(row):
        if row.get("是否可写入") == "否":
            return ["background-color: #fee2e2"] * len(row)
        if "可能是昨日数据" in str(row.get("提示", "")):
            return ["background-color: #fff7ed"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(_shade, axis=1), use_container_width=True, hide_index=True)


def render_alipay_json_sync(records):
    st.markdown("#### 支付宝 JSON 自动同步")
    st.caption("请粘完整的支付宝场外基金持仓列表。确认写入后，只替换支付宝场外基金，不影响银河、东方财富。")

    with st.expander("📷 怎么获取JSON？点开看步骤", expanded=False):
        st.write("① 截图支付宝/养基宝『我的持有』页面 → ② 发给任意能读图的AI，并复制下面这段提示词一起发 → ③ 把AI返回的JSON粘到下方框 → ④ 点解析并同步")
        st.code(ALIPAY_JSON_PROMPT, language=None)

    raw_key = "alipay_json_raw"
    items_key = "alipay_json_items"
    st.session_state.setdefault(raw_key, "")

    raw = st.text_area(
        "粘贴持仓JSON",
        height=150,
        key=raw_key,
        placeholder='例如：[{"name":"永赢高端装备智选混合A","share_class":"A","amount":15217.89,"profit":-3382.38,"profit_rate":-18.18,"account":"支付宝","today_updated":true}]',
    )
    if st.button("解析并同步", type="primary", key="alipay_json_parse"):
        try:
            items = parse_alipay_json(raw)
            if not items:
                st.session_state[items_key] = []
                st.warning("没有解析到任何基金。")
            else:
                st.session_state[items_key] = items
                st.success(f"已解析 {len(items)} 只基金，请先核对预览，确认后才会写入。")
        except Exception as e:
            st.session_state[items_key] = []
            st.error(f"JSON 解析失败：{type(e).__name__}：{e}")

    items = st.session_state.get(items_key, [])
    if not items:
        st.caption("提示：反推份额因金额已四舍五入到分，会与支付宝实际份额有零点几份微差，属正常。")
        return

    options = alipay_code_options(records)
    option_codes = [""] + [opt["code"] for opt in options]
    selected_codes = {}

    st.subheader("代码匹配")
    for idx, item in enumerate(items):
        auto_code = match_alipay_code(item, records)
        key = f"alipay_json_code_{idx}"
        if key not in st.session_state:
            st.session_state[key] = auto_code if auto_code in option_codes else ""
        selected = st.selectbox(
            f"{item.get('name', '')} 对应代码",
            option_codes,
            format_func=lambda c, opts=options: _alipay_code_label(c, opts),
            key=key,
        )
        manual_code = st.text_input(
            "如果上面没有这只基金，请手动填 6 位代码",
            value="",
            key=f"alipay_json_manual_code_{idx}",
            placeholder="例如：015789",
        ).strip()
        selected_codes[idx] = manual_code or selected

    with st.spinner("正在按净值反推份额和成本..."):
        preview_rows, proposed = build_alipay_preview(items, records, selected_codes)

    st.subheader("预览确认")
    _render_alipay_preview_table(preview_rows)
    st.caption("已知特性：反推份额因金额已四舍五入到分，会与支付宝实际份额有零点几份微差，属正常。")

    invalid_rows = [r for r in preview_rows if r.get("是否可写入") != "是"]
    if invalid_rows:
        st.error("还有记录不能写入，请先手选代码或修正 JSON。")
    final_records = merge_records(records, proposed, "replace_same_account_type") if proposed else records
    with st.expander("差异预览", expanded=False):
        render_diff_preview(diff_records(records, final_records))

    checked = st.checkbox("我已核对代码、金额、收益、反推份额和成本", key="alipay_json_checked")
    can_write = bool(proposed) and not invalid_rows and checked
    if st.button("确认写入支付宝持仓", disabled=not can_write, key="alipay_json_write"):
        try:
            backup, sync_ok, sync_msg = save_managed_holdings(final_records)
            if sync_ok:
                st.session_state[items_key] = []
                st.session_state[raw_key] = ""
            handle_holdings_save_result(backup, sync_ok, sync_msg)
        except Exception as e:
            st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
            st.error(f"写入流程异常：{type(e).__name__}：{e}")
            render_github_sync_diag(expanded=False)


def render_holding_manager():
    st.subheader("持仓管理")
    st.caption("这里会直接写入 holdings_data.json，并在保存前自动备份上一版。")
    notice = st.session_state.pop("holdings_save_notice", None)
    if notice:
        level, message, backup_text = notice
        if level == "success":
            st.success(message)
            if backup_text:
                st.caption(f"旧文件已备份到：{backup_text}")
        else:
            st.error(message)
    render_github_sync_diag(expanded=False)
    records = current_holdings()
    accounts = ["银河证券", "东方财富", "支付宝"]
    for acc in accounts:
        st.markdown(f"### {acc}")
        account_rows = [(i, r) for i, r in enumerate(records) if r.get("account") == acc]
        if not account_rows:
            st.caption("这个账户暂时没有持仓。")
        for idx, r in account_rows:
            title = f"{r.get('name', '未命名')}（{r.get('code', '')}）"
            with st.expander(title, expanded=False):
                form_key = f"edit_{idx}_{r.get('account')}_{r.get('code')}"
                with st.form(form_key):
                    type_label = TYPE_LABELS.get(r.get("type"), "A股个股")
                    type_choice = st.selectbox(
                        "资产类型",
                        list(LABEL_TYPES.keys()),
                        index=list(LABEL_TYPES.keys()).index(type_label) if type_label in LABEL_TYPES else 0,
                        key=f"{form_key}_type",
                    )
                    c1, c2 = st.columns(2)
                    name = c1.text_input("名称", value=str(r.get("name", "")), placeholder="例如：中芯国际")
                    code = c2.text_input("代码", value=str(r.get("code", "")), placeholder="例如：688981")
                    share_class = st.text_input("份额类别（可选，A/C/空）", value=str(r.get("share_class", "") or ""), placeholder="例如：A")
                    new_type = LABEL_TYPES[type_choice]
                    if new_type in {"stock", "lof", "otc"}:
                        c3, c4 = st.columns(2)
                        cost = c3.number_input("单位成本（每股/每份）", value=float(unit_cost_of(r) or 0), step=0.001, format="%.4f")
                        shares = c4.number_input("持有份额", value=float(r.get("shares", 0) or 0), step=1.0)
                        buy_date = st.text_input("首次买入日期（可选）", value=str(r.get("buy_date", "")), placeholder="例如：2026-05-29", key=f"{form_key}_buy_date")
                        raw = {"account": acc, "type": new_type, "name": name, "code": code, "share_class": share_class, "unit_cost": cost, "shares": shares, "buy_date": buy_date}
                        if new_type == "otc" and r.get("market_value") not in (None, "") and r.get("shares") in (None, ""):
                            st.caption("这只场外基金还是旧格式，请填入支付宝页面里的“持有份额”和“成本价”后保存。")
                    else:
                        c3, c4 = st.columns(2)
                        market_value = c3.number_input("当前市值（元）", value=float(r.get("market_value", 0) or 0), step=100.0)
                        profit = c4.number_input("持有收益（元）", value=float(r.get("profit", 0) or 0), step=100.0)
                        raw = {"account": acc, "type": new_type, "name": name, "code": code, "market_value": market_value, "profit": profit}
                    submitted = st.form_submit_button("保存这只")
                c_del, _ = st.columns([1, 3])
                delete = c_del.button("删除这只", key=f"delete_{idx}_{r.get('code')}")
                if submitted:
                    try:
                        updated = list(records)
                        updated[idx] = clean_managed_record(raw)
                        backup, sync_ok, sync_msg = save_managed_holdings(updated)
                        handle_holdings_save_result(backup, sync_ok, sync_msg)
                    except Exception as e:
                        st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
                        st.error(f"保存流程异常：{type(e).__name__}：{e}")
                        render_github_sync_diag(expanded=False)
                if delete:
                    try:
                        updated = [x for i, x in enumerate(records) if i != idx]
                        backup, sync_ok, sync_msg = save_managed_holdings(updated)
                        handle_holdings_save_result(backup, sync_ok, sync_msg)
                    except Exception as e:
                        st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
                        st.error(f"删除流程异常：{type(e).__name__}：{e}")
                        render_github_sync_diag(expanded=False)

        with st.expander(f"添加一只到 {acc}", expanded=False):
            add_key = f"add_{acc}"
            with st.form(add_key):
                type_choice = st.selectbox("资产类型", list(LABEL_TYPES.keys()), key=f"{add_key}_type")
                c1, c2 = st.columns(2)
                name = c1.text_input("名称", placeholder="例如：中芯国际")
                code = c2.text_input("代码", placeholder="例如：688981")
                share_class = st.text_input("份额类别（可选，A/C/空）", placeholder="例如：A")
                new_type = LABEL_TYPES[type_choice]
                if new_type in {"stock", "lof", "otc"}:
                    c3, c4 = st.columns(2)
                    cost = c3.number_input("单位成本（每股/每份）", min_value=0.0, step=0.001, format="%.4f")
                    shares = c4.number_input("持有份额", min_value=0.0, step=1.0)
                    buy_date = st.text_input("首次买入日期（可选）", placeholder="例如：2026-05-29", key=f"{add_key}_buy_date")
                    raw = {"account": acc, "type": new_type, "name": name, "code": code, "share_class": share_class, "unit_cost": cost, "shares": shares, "buy_date": buy_date}
                else:
                    c3, c4 = st.columns(2)
                    market_value = c3.number_input("当前市值（元）", min_value=0.0, step=100.0)
                    profit = c4.number_input("持有收益（元）", step=100.0)
                    raw = {"account": acc, "type": new_type, "name": name, "code": code, "market_value": market_value, "profit": profit}
                add = st.form_submit_button("添加并保存")
            if add:
                try:
                    updated = records + [clean_managed_record(raw)]
                    backup, sync_ok, sync_msg = save_managed_holdings(updated)
                    handle_holdings_save_result(backup, sync_ok, sync_msg)
                except Exception as e:
                    st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
                    st.error(f"添加流程异常：{type(e).__name__}：{e}")
                    render_github_sync_diag(expanded=False)
        st.divider()


def _account_code_label(code, options):
    if not code:
        return "请选择/手填代码"
    for opt in options:
        if opt["code"] == code:
            return f"{code}｜{opt.get('name', '')}"
    return code


def _render_account_import_preview(rows):
    if not rows:
        st.info("还没有可预览的记录。")
        return
    df = pd.DataFrame(rows)

    def _shade(row):
        if row.get("是否可写入") == "否":
            return ["background-color: #fee2e2"] * len(row)
        if row.get("状态") != "可写入":
            return ["background-color: #fff7ed"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(_shade, axis=1), use_container_width=True, hide_index=True)


def account_import_widget():
    account = st.selectbox("账户", ["支付宝", "银河证券", "东方财富"], key="account_import_account")
    st.caption("先把截图发给能读图的 AI，复制本页提示词；再把 AI 返回的 JSON 粘到下面。系统只在你确认后写入。")
    with st.expander("读图提示词（点开复制）", expanded=True):
        st.code(ACCOUNT_IMPORT_PROMPTS[account], language=None)

    raw_key = f"account_import_raw_{account}"
    parsed_key = f"account_import_items_{account}"
    st.session_state.setdefault(raw_key, "")
    if st.button("载入示例JSON", key=f"account_import_sample_{account}"):
        st.session_state[raw_key] = ACCOUNT_IMPORT_SAMPLES[account]
        st.session_state[parsed_key] = []

    raw = st.text_area("粘贴JSON", height=190, key=raw_key)
    if st.button("解析并预览", type="primary", key=f"account_import_parse_{account}"):
        try:
            items = parse_account_json(raw, account)
            st.session_state[parsed_key] = items
            if items:
                st.success(f"已解析 {len(items)} 条，请核对代码和预览。")
            else:
                st.warning("没有解析到持仓。")
        except Exception as e:
            st.session_state[parsed_key] = []
            st.error(f"解析失败：{type(e).__name__}：{e}")

    items = st.session_state.get(parsed_key, [])
    if not items:
        return

    records = current_holdings()
    base_options = account_code_options(records, account)
    base_codes = [""] + [opt["code"] for opt in base_options]
    selected_codes = {}
    st.subheader("代码核对")
    for idx, item in enumerate(items):
        auto_code = match_code(item, account, records)
        option_codes = list(base_codes)
        if auto_code and auto_code not in option_codes:
            option_codes.append(auto_code)
        key = f"account_import_code_{account}_{idx}"
        if key not in st.session_state:
            st.session_state[key] = auto_code if auto_code in option_codes else ""
        selected = st.selectbox(
            f"{item.get('name', '')} 对应代码",
            option_codes,
            format_func=lambda c, opts=base_options: _account_code_label(c, opts),
            key=key,
        )
        manual = st.text_input(
            "如果上面没有，请手动填 6 位代码",
            key=f"account_import_manual_{account}_{idx}",
            placeholder="例如：600961",
        ).strip()
        selected_codes[idx] = manual or selected

    with st.spinner("正在取最新净值/行情并生成预览..."):
        preview_rows, proposed, merge_notes = build_account_preview(items, account, records, selected_codes)

    st.subheader("预览确认")
    if merge_notes:
        for note in merge_notes:
            st.info(note)
    _render_account_import_preview(preview_rows)
    invalid_rows = [r for r in preview_rows if r.get("是否可写入") != "是"]
    if invalid_rows:
        st.error("还有记录不能写入：请先修正代码、份额、成本或市值偏差。")

    final_records = merge_records(records, proposed, "replace_same_account_type") if proposed else records
    with st.expander("差异预览", expanded=False):
        render_diff_preview(diff_records(records, final_records))

    checked = st.checkbox("我已核对代码、份额、单位成本和市值偏差", key=f"account_import_checked_{account}")
    if st.button("确认写入并同步", disabled=bool(invalid_rows) or not proposed or not checked, key=f"account_import_write_{account}"):
        try:
            backup, sync_ok, sync_msg = save_managed_holdings(final_records)
            if sync_ok:
                st.session_state[parsed_key] = []
                st.session_state[raw_key] = ""
            handle_holdings_save_result(backup, sync_ok, sync_msg)
        except Exception as e:
            st.session_state["github_sync_diag"] = {"异常": f"{type(e).__name__}：{e}"}
            st.error(f"写入流程异常：{type(e).__name__}：{e}")
            render_github_sync_diag(expanded=False)


def render_home(df, exposure, board_source, snapshot_history):
    total_mv = df["市值"].sum()
    total_pnl = df["盈亏"].sum()
    total_cost = df["成本额"].sum()
    today_pnl = pd.to_numeric(df["今日估算盈亏"], errors="coerce").sum(min_count=1)
    total_rate = total_pnl / total_cost * 100 if total_cost else 0

    c = st.columns(3)
    with c[0]:
        card("总资产", esc(fmt_money(total_mv)), "当前三账户合计")
    with c[1]:
        card("今日估算盈亏", value_html(today_pnl, " 元", signed=True), "场外基金可能晚间更新", cls(today_pnl))
    with c[2]:
        card("累计盈亏", value_html(total_pnl, " 元", signed=True), f"{total_rate:+.1f}%", cls(total_pnl))

    acc_cols = st.columns(3)
    for col, acc in zip(acc_cols, ["银河证券", "东方财富", "支付宝"]):
        sub = df[df["account"] == acc]
        mv = sub["市值"].sum()
        pnl = sub["盈亏"].sum()
        with col:
            card(acc, esc(fmt_money(mv)), f'<span class="{cls(pnl)}">{pnl:+,.0f} 元</span>')

    st.divider()
    if len(exposure):
        biggest = exposure.iloc[0]
        hot_part = exposure.dropna(subset=["近期温度"])
        hot = hot_part.sort_values("近期温度", ascending=False).iloc[0] if len(hot_part) else None
        c = st.columns(2)
        with c[0]:
            card("最大持仓板块", esc(biggest["板块"]), f"占总资产 {biggest['占总资产比例']:.1f}%")
        with c[1]:
            if hot is not None:
                card("近期最热持仓板块", esc(hot["板块"]), f"近期温度 {hot['近期温度']:.0f}｜{hot['近期情绪']}")
            else:
                card("近期最热持仓板块", "—", "历史不足")
        high_risk = exposure[(exposure["占总资产比例"] >= 20) & (exposure["近期温度"] >= 75)]
        if len(high_risk):
            r = high_risk.iloc[0]
            st.warning(f"{r['板块']} 当前属于“高仓位 + 近期高热度”组合，请关注波动风险。该提示不构成买卖建议。")
        else:
            st.info("当前未识别到明显“高仓位 + 近期高热度”组合。")
    else:
        st.info("当前还没有可展示的持仓板块数据。")
    st.subheader("收益曲线")
    render_performance_curve(snapshot_history, "total")
    st.caption(f"数据更新于：{board_source}")
    with st.expander("数据详情", expanded=False):
        st.dataframe(health_summary(df, board_source, using_old_boards), use_container_width=True, hide_index=True)
    with st.expander("今日大白话复盘", expanded=False):
        st.write(daily_recap(exposure, total_mv))
    risk_notice(st)


def render_holding_overview(df, snapshot_history):
    total_mv = df["市值"].sum()
    total_pnl = df["盈亏"].sum()
    total_cost = df["成本额"].sum()
    rate = total_pnl / total_cost * 100 if total_cost else 0
    c = st.columns(3)
    with c[0]:
        card("总资产", esc(fmt_money(total_mv)), "")
    with c[1]:
        card("总盈亏", value_html(total_pnl, " 元", signed=True), "", cls(total_pnl))
    with c[2]:
        card("总收益率", f'<span class="{cls(rate)}">{rate:+.1f}%</span>', "")

    st.subheader("收益曲线")
    render_performance_curve(snapshot_history, "total")

    st.subheader("账户小计")
    cols = st.columns(3)
    for col, acc in zip(cols, ["银河证券", "东方财富", "支付宝"]):
        sub = df[df["account"] == acc]
        pnl = sub["盈亏"].sum()
        with col:
            card(acc, esc(fmt_money(sub["市值"].sum())), f'<span class="{cls(pnl)}">{pnl:+,.0f} 元</span>')

    st.subheader("资产类型")
    cols = st.columns(3)
    for col, (t, name) in zip(cols, [("stock", "A股"), ("lof", "场内基金"), ("otc", "场外基金")]):
        sub = df[df["type"] == t]
        with col:
            card(name, esc(fmt_money(sub["市值"].sum())), f"{sub['市值'].sum()/total_mv*100 if total_mv else 0:.1f}%")

    st.subheader("全部持仓简表")
    show = df.copy()
    show["市值"] = show["市值"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
    show["盈亏"] = show["盈亏"].map(lambda v: "—" if pd.isna(v) else f"{v:+,.0f}")
    show["盈亏率"] = show["盈亏率"].map(lambda v: "—" if pd.isna(v) else f"{v:+.1f}%")
    st.dataframe(
        show[["account", "name", "code", "市值", "盈亏", "盈亏率", "数据日期"]].rename(
            columns={"account": "账户", "name": "名称", "code": "代码"}
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_account(df, account, with_chart, snapshot_history):
    sub = df[df["account"] == account]
    total_mv = sub["市值"].sum()
    total_pnl = sub["盈亏"].sum()
    total_cost = sub["成本额"].sum()
    rate = total_pnl / total_cost * 100 if total_cost else 0
    c = st.columns(3)
    with c[0]:
        card("账户市值", esc(fmt_money(total_mv)), account)
    with c[1]:
        card("账户盈亏", value_html(total_pnl, " 元", signed=True), "", cls(total_pnl))
    with c[2]:
        card("收益率", f'<span class="{cls(rate)}">{rate:+.1f}%</span>', "")

    st.subheader("收益曲线")
    scope = {"银河证券": "galaxy", "东方财富": "eastmoney", "支付宝": "alipay"}[account]
    render_performance_curve(snapshot_history, scope)

    for _, r in sub.iterrows():
        holding_card(r, with_chart=with_chart, otc=(r["type"] == "otc"))


def render_holdings(df, snapshot_history, fund_map, live, board_history):
    if st.session_state.get("detail_code"):
        render_holding_detail(df, snapshot_history, fund_map, live, board_history)
    else:
        render_holdings_list(df, snapshot_history, fund_map, live)


def render_radar(live, board_source, using_old):
    render_glossary(st, PAGE_TERMS["radar"])
    if live is None or len(live) == 0:
        st.error("板块数据暂时不可用。")
        return
    st.caption(f"{board_source}｜旧数据：{'是' if using_old else '否'}")
    top = live.sort_values("情绪温度分", ascending=False).head(15)
    rising = live.dropna(subset=["温度变化"])
    rising = rising[rising["温度变化"] > 0].sort_values("温度变化", ascending=False).head(10)
    cooling = live.dropna(subset=["温度变化"])
    cooling = cooling[(cooling["情绪温度分"] >= 60) & (cooling["温度变化"] < 0)].sort_values("温度变化").head(10)

    st.subheader("温度排行 Top15")
    mini_table(board_trend_table(top))
    st.subheader("升温榜")
    mini_table(board_trend_table(rising))
    st.subheader("高位降温榜")
    mini_table(board_trend_table(cooling))

    with st.expander("温度分拆解 / 数据可信度 / 历史趋势", expanded=False):
        board = st.selectbox("选择板块", live.sort_values("板块")["板块"].tolist())
        row = live[live["板块"] == board].iloc[0]
        radar_metric_strip([
            ("温度分", f"{row['情绪温度分']:.0f}"),
            ("情绪标签", row["情绪标签"]),
            ("可信度", row["数据可信度"]),
            ("趋势", row["趋势箭头"]),
        ])
        score_breakdown_grid(row)
    risk_notice(st)


def render_my_boards(exposure, fund_map, recent_note):
    render_glossary(st, PAGE_TERMS["mine"])
    if len(exposure) == 0:
        st.error("暂无持仓板块数据。")
        return
    st.caption(f"近期情绪口径：{recent_note} 这是用板块涨跌、资金流、上涨家数和成交额估算的拥挤度，不直接抓社交媒体。")
    show = exposure.copy()
    show["_a_share_first"] = show["温度板块"].map(lambda x: 1 if valid_board_name(x) else 0)
    show = show.sort_values(["_a_share_first", "持仓市值"], ascending=[False, False]).reset_index(drop=True)
    show["占比"] = show["占总资产比例"].map(lambda v: f"{v:.1f}%")
    show["近期温度"] = show["近期温度"].map(lambda v: "—" if pd.isna(v) else f"{v:.0f}")
    st.dataframe(
        show[["板块", "占比", "近期温度", "近期情绪"]].rename(columns={"近期情绪": "状态"}),
        use_container_width=True,
        hide_index=True,
    )
    high_risk = exposure[(exposure["占总资产比例"] >= 20) & (exposure["近期温度"] >= 75)]
    if len(high_risk):
        r = high_risk.iloc[0]
        st.warning(f"{r['板块']} 属于“高仓位 + 近期高热度”组合，请关注波动和回撤风险。该提示不构成买卖建议。")
    else:
        st.info("当前未识别到明显“高仓位 + 近期高热度”组合。")

    with st.expander("当日温度参考", expanded=False):
        day = exposure.copy()
        day["当日温度"] = day["温度分"].map(lambda v: "—" if pd.isna(v) else f"{v:.0f}")
        st.dataframe(
            day[["板块", "温度板块", "当日温度", "情绪标签", "数据可信度", "趋势"]],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("情景模拟", expanded=False):
        sims = []
        for _, r in exposure.head(6).iterrows():
            for drop in (5, 10):
                impact = r["占总资产比例"] * drop / 100
                sims.append({"情景": f"{r['板块']} 下跌 {drop}%", "总资产估算影响": f"约回撤 {impact:.1f}%"})
        st.dataframe(pd.DataFrame(sims), use_container_width=True, hide_index=True)
        st.caption("静态估算，不代表真实未来涨跌，也不构成买卖建议。")

    with st.expander("基金穿透明细", expanded=False):
        fund_rows = []
        fund_holdings = [h for h in HOLDINGS if str(h.get("type", "")) in ("lof", "otc")]
        for holding in fund_holdings:
            code = str(holding.get("code", ""))
            if not code:
                continue
            clean_code = clean_stock_code(code)
            fm = fund_map.get(code, {})
            raw_board = fm.get("main_board") or BOARD_MAP.get(code, ["", "None"])[1]
            board = normalize_board_name(raw_board)
            detail = fm.get("detail", "")
            override = fund_board_override(clean_code)
            if override:
                main_board = override[0]
                note = "境内A股基金，根据前十大重仓估算。"
            elif is_true_non_a_fund(clean_code):
                main_board = "境外/非A股"
                note = "该基金主要投资非A股市场，无法直接使用A股板块温度衡量；净值会按基金披露节奏滞后更新。"
            elif valid_board_name(board):
                main_board = board
                note = "根据前十大重仓估算。"
            else:
                main_board = "穿透暂缺"
                note = "这是境内基金，但暂时没有取得足够A股重仓穿透数据，先不做盘中估值。"
            fund_rows.append({
                "基金": f"{holding['name']} {code}",
                "主要板块": main_board,
                "估算占比": extract_weight_text(detail),
                "说明": note,
                "明细": detail,
            })
        if fund_rows:
            for item in fund_rows:
                st.markdown(
                    f"""
                    <div class="holding-card">
                        <div class="holding-title">{esc(item["基金"])}</div>
                        <div class="holding-meta">主要板块：{esc(item["主要板块"])}</div>
                        <div class="kv-grid">
                            <div class="kv"><div class="kv-label">估算占比</div><div class="kv-value">{esc(item["估算占比"])}</div></div>
                            <div class="kv"><div class="kv-label">说明</div><div class="kv-value">{esc(item["说明"])}</div></div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("暂无基金穿透明细。")
        for _, r in exposure[exposure["说明"].astype(str) != ""].iterrows():
            st.markdown(f"**{r['板块']}**")
            st.write(r["说明"][:900] + ("..." if len(r["说明"]) > 900 else ""))
    risk_notice(st)


def render_advanced(df, board_source, using_old, fund_map):
    st.caption("这里放维护功能，日常看盘不用打开。")
    section = st.selectbox("高级功能", ["持仓管理", "持仓导入", "数据更新日志", "基金穿透明细", "JSON/CSV 示例"], label_visibility="collapsed")
    if section == "持仓管理":
        render_holding_manager()
    elif section == "持仓导入":
        account_import_widget()
    elif section == "数据更新日志":
        health = health_summary(df, board_source, using_old)
        st.dataframe(health, use_container_width=True, hide_index=True)
        log = load_update_log()
        if len(log):
            st.dataframe(log[["update_time", "task_name", "status", "data_date", "retry_count", "error_msg"]], use_container_width=True, hide_index=True)
        else:
            st.warning("暂无 update_log 记录。")
    elif section == "基金穿透明细":
        rows = []
        for code, fm in fund_map.items():
            rows.append({"代码": code, "主板块": fm.get("main_board", ""), "明细": fm.get("detail", "")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        with st.expander("通用 JSON / CSV 示例", expanded=True):
            st.code(SAMPLE_JSON, language="json")
            st.code(SAMPLE_CSV, language="csv")
        with st.expander("视觉模型提示词", expanded=False):
            st.code(VISION_PROMPT_EASTMONEY_A_STOCK, language="text")
            st.code(VISION_PROMPT_GALAXY_LOF, language="text")
            st.code(VISION_PROMPT_ALIPAY_OTC, language="text")
    risk_notice(st)


df = compute(market_cache_key())
snapshot_history = load_snapshot_history()
fund_map = load_fund_board_map()
try:
    board_history = load_board_history()
    recent_sentiment, recent_note = build_recent_sentiment(board_history)
    raw_boards, board_source, using_old_boards = boards_live()
    live = score_boards(raw_boards, board_history)
except Exception as e:
    board_history = pd.DataFrame()
    recent_sentiment, recent_note = {}, f"历史不足：{e}"
    live = None
    board_source = f"失败：{e}"
    using_old_boards = True

exposure = build_exposure(df, fund_map, live, recent_sentiment) if live is not None else build_exposure(df, fund_map, None, recent_sentiment)

try:
    query = st.query_params
    should_clear_query = False
    if query.get("page") == "holding":
        st.session_state["main_page"] = "持仓"
        should_clear_query = True
    if query.get("detail_code"):
        st.session_state["main_page"] = "持仓"
        st.session_state["detail_code"] = str(query.get("detail_code", ""))
        st.session_state["detail_key"] = str(query.get("detail_key", ""))
        should_clear_query = True
    if query.get("hold_sort"):
        st.session_state["main_page"] = "持仓"
        sort_map = {"today": "今日估算盈亏", "profit": "盈亏"}
        sort_key = sort_map.get(str(query.get("hold_sort")))
        if sort_key:
            st.session_state["holding_sort_by"] = sort_key
            st.session_state["holding_sort_desc"] = str(query.get("hold_desc", "1")) == "1"
        should_clear_query = True
    if should_clear_query:
        st.query_params.clear()
except Exception:
    pass

st.title("我的全资产管理台")

page_options = ["首页", "持仓", "板块雷达", "我的板块", "高级功能"]
page_kwargs = {"default": "首页"} if "main_page" not in st.session_state else {}
page = st.segmented_control(
    "页面",
    page_options,
    label_visibility="collapsed",
    width="stretch",
    key="main_page",
    **page_kwargs,
)

if page == "首页":
    render_home(df, exposure, board_source, snapshot_history)
elif page == "持仓":
    render_holdings(df, snapshot_history, fund_map, live, board_history)
elif page == "板块雷达":
    render_radar(live, board_source, using_old_boards)
elif page == "我的板块":
    render_my_boards(exposure, fund_map, recent_note)
else:
    render_advanced(df, board_source, using_old_boards, fund_map)

# -*- coding: utf-8 -*-
"""持仓数据读取层。

云端优先读取 HOLDINGS_DATA_JSON 加密变量；本地没有该变量时回退读取 holdings_data.json。
这个文件只保留读取逻辑，方便代码安全提交到 Git。
"""
import json
import os

from project_paths import HOLDINGS_DATA_FILE

DEFAULT_DATA = {
    "holdings": [],
    "board_map": {},
}


def _secret_text():
    raw = os.environ.get("HOLDINGS_DATA_JSON")
    if raw:
        return raw
    try:
        import streamlit as st

        raw = st.secrets.get("HOLDINGS_DATA_JSON", "")
        return raw or ""
    except Exception:
        return ""


def _normalize_data(data):
    holdings = data.get("holdings", [])
    board_map = {
        str(code): tuple(value) if isinstance(value, list) else value
        for code, value in data.get("board_map", {}).items()
    }
    return holdings, board_map


def load_holdings_data():
    raw = _secret_text()
    try:
        if raw:
            return _normalize_data(json.loads(raw))
        if not HOLDINGS_DATA_FILE.exists():
            return [], {}
        data = json.loads(HOLDINGS_DATA_FILE.read_text(encoding="utf-8"))
        return _normalize_data(data)
    except Exception:
        return [], {}


HOLDINGS, BOARD_MAP = load_holdings_data()

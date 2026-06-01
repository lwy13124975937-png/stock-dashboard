# -*- coding: utf-8 -*-
"""持仓数据读取层。

优先读取仓库工作目录里的 holdings_data.json；没有该文件时回退读取 HOLDINGS_DATA_JSON 加密变量。
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
    try:
        if HOLDINGS_DATA_FILE.exists():
            data = json.loads(HOLDINGS_DATA_FILE.read_text(encoding="utf-8"))
            return _normalize_data(data)
    except Exception:
        pass
    try:
        raw = _secret_text()
        if raw:
            return _normalize_data(json.loads(raw))
    except Exception:
        pass
    return _normalize_data(DEFAULT_DATA)


HOLDINGS, BOARD_MAP = load_holdings_data()

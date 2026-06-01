# -*- coding: utf-8 -*-
"""项目路径统一配置。

默认使用当前文件所在目录；上云时可用环境变量 STOCK_ROOT 指向项目目录。
"""
import os
from pathlib import Path

ROOT = Path(os.environ.get("STOCK_ROOT") or Path(__file__).resolve().parent).resolve()
DB = str(ROOT / "stock_data.db")
HOLDINGS_DATA_FILE = ROOT / "holdings_data.json"
BACKUP_DIR = ROOT / "backups"
HISTORY_DIR = ROOT / "history"
SNAPSHOTS_FILE = HISTORY_DIR / "snapshots.csv"
BOARD_HEAT_HISTORY_FILE = HISTORY_DIR / "board_heat.csv"
FUND_BOARD_MAP_FILE = ROOT / "fund_board_map_cache.json"

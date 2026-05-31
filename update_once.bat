@echo off
cd /d C:\stock
python update_data.py
python build_fund_map.py
pause

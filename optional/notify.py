# -*- coding: utf-8 -*-
"""简单提醒工具：先支持 PushPlus 和邮件，供后续上云定时任务调用。"""
import json
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).with_name("notify_config.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "pushplus_token": "",
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 465,
        "username": "",
        "password": "",
        "to": "",
    },
}


def ensure_config():
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_config()


def load_config():
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    cfg["email"] = {**DEFAULT_CONFIG["email"], **data.get("email", {})}
    return cfg


def send_pushplus(token, title, content):
    if not token:
        return False, "PushPlus token 为空"
    r = requests.post(
        "http://www.pushplus.plus/send",
        json={"token": token, "title": title, "content": content, "template": "html"},
        timeout=20,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    return True, r.text[:120]


def send_email(email_cfg, title, content):
    if not email_cfg.get("enabled"):
        return False, "邮件未启用"
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = email_cfg["username"]
    msg["To"] = email_cfg["to"]
    with smtplib.SMTP_SSL(email_cfg["smtp_host"], int(email_cfg["smtp_port"]), timeout=20) as smtp:
        smtp.login(email_cfg["username"], email_cfg["password"])
        smtp.sendmail(email_cfg["username"], [email_cfg["to"]], msg.as_string())
    return True, "邮件已发送"


def notify(title, content):
    cfg = ensure_config()
    if not cfg.get("enabled"):
        return [(False, "提醒总开关未启用")]
    results = []
    if cfg.get("pushplus_token"):
        results.append(send_pushplus(cfg["pushplus_token"], title, content))
    if cfg.get("email", {}).get("enabled"):
        results.append(send_email(cfg["email"], title, content))
    if not results:
        results.append((False, "没有配置可用提醒渠道"))
    return results

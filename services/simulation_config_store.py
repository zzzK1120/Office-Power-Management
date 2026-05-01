"""模拟全局参数持久化（写入 instance，重启后仍生效）"""
from __future__ import annotations

import json
import os

from flask import Flask

SIM_CONFIG_KEYS = [
    'SIM_SAMPLE_INTERVAL_SECONDS',
    'SIM_VOLTAGE_MIN',
    'SIM_VOLTAGE_MAX',
    'SIM_POWER_MIN',
    'SIM_POWER_MAX',
    'SIM_STANDBY_POWER_MAX',
    'SIM_ANOMALY_PROB_VOLTAGE',
    'SIM_ANOMALY_PROB_POWER',
]


def _path(app: Flask) -> str:
    return os.path.join(app.instance_path, 'simulation_global.json')


def load_into_app(app: Flask) -> None:
    """启动时合并已保存的全局模拟参数到 app.config。"""
    path = _path(app)
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for k in SIM_CONFIG_KEYS:
        if k not in data or data[k] is None:
            continue
        try:
            if k == 'SIM_SAMPLE_INTERVAL_SECONDS':
                app.config[k] = int(data[k])
            else:
                app.config[k] = float(data[k])
        except (TypeError, ValueError):
            continue


def save_from_app(app: Flask) -> None:
    """将当前内存中的模拟全局参数写入文件。"""
    try:
        os.makedirs(app.instance_path, exist_ok=True)
    except OSError:
        return
    path = _path(app)
    data = {k: app.config.get(k) for k in SIM_CONFIG_KEYS}
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

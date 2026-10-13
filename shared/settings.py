"""写配置（自动回复开关等）。settings.yaml 读写。"""
import threading
from pathlib import Path

import yaml

from shared.config import ROOT

_lock = threading.Lock()

SETTINGS_PATH = ROOT / "config" / "settings.yaml"


def load() -> dict:
    if SETTINGS_PATH.exists():
        return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    return {}


def get(path: str, default=None):
    """按点路径取值，如 get('write.auto_reply.enabled')。"""
    node = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_value(path: str, value) -> dict:
    """按点路径写值并落盘（M6：锁内完成，防并发写坏 YAML）。返回更新后的全量配置。"""
    with _lock:
        cfg = load()
        node = cfg
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        SETTINGS_PATH.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return cfg

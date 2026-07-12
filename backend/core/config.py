"""設定ファイル読込。config.yaml から読み取り、${ENV_VAR} を環境変数で解決する。"""

import os
import re
from pathlib import Path
from typing import Any

import yaml


def _resolve_env(value: Any) -> Any:
    """文字列中の ${VAR} を環境変数で置換。再帰的に処理。"""
    if isinstance(value, str):
        def replacer(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return re.sub(r"\$\{(\w+)\}", replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> dict:
    """config.yaml を読み込み、環境変数を解決して返す。

    path が未指定の場合は、このファイルからの相対パスで config.yaml を探す。
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return _resolve_env(raw)


def update_config_yaml(config_path: Path, mutator) -> dict:
    """config.yaml を読み込み、mutatorで書き換えてから保存する。

    Args:
        config_path: 対象のconfig.yamlパス
        mutator: raw dict を直接書き換える呼び出し可能オブジェクト

    Returns:
        更新後のraw dict
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    mutator(raw)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, sort_keys=False)
    return raw

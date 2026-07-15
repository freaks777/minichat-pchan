"""設定ファイル読込。config.yaml から読み取り、${ENV_VAR} を環境変数で解決する。"""

import os
import re
import math
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def _validate_keys(values: dict, allowed: set[str], section: str) -> None:
    if not isinstance(values, dict):
        raise ValueError(f"{section} must be an object")
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unknown {section} setting(s): {', '.join(unknown)}")


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def validate_api_settings(values: dict) -> dict:
    """Validate and normalize the API settings accepted by the settings UI."""
    _validate_keys(values, {"max_tokens", "temperature", "timeout"}, "api")
    result = {}
    if "max_tokens" in values:
        result["max_tokens"] = _bounded_int(values["max_tokens"], "max_tokens", 100, 100000)
    if "temperature" in values:
        result["temperature"] = _bounded_number(values["temperature"], "temperature", 0.0, 2.0)
    if "timeout" in values:
        result["timeout"] = _bounded_int(values["timeout"], "timeout", 10, 600)
    return result


def validate_session_settings(values: dict) -> dict:
    """Validate and normalize history/context persistence settings."""
    _validate_keys(values, {"max_tokens", "save_interval"}, "session")
    result = {}
    if "max_tokens" in values:
        result["max_tokens"] = _bounded_int(values["max_tokens"], "max_tokens", 4000, 200000)
    if "save_interval" in values:
        result["save_interval"] = _bounded_int(values["save_interval"], "save_interval", 1, 100)
    return result


def validate_style_settings(values: dict) -> dict:
    """Validate the three supported global style axes without truthy coercion."""
    _validate_keys(values, {"viewpoint", "narration", "person"}, "style")
    result = {}
    if "viewpoint" in values:
        if values["viewpoint"] not in {"ai_character", "user_character"}:
            raise ValueError("viewpoint must be ai_character or user_character")
        result["viewpoint"] = values["viewpoint"]
    if "narration" in values:
        if type(values["narration"]) is not bool:
            raise ValueError("narration must be a boolean")
        result["narration"] = values["narration"]
    if "person" in values:
        if values["person"] not in {"first", "third"}:
            raise ValueError("person must be first or third")
        result["person"] = values["person"]
    return result


def validate_watchdog_settings(values: dict) -> dict:
    """Validate watchdog settings and each escalation level."""
    _validate_keys(values, {"enabled", "check_interval", "levels"}, "watchdog")
    result = {}
    if "enabled" in values:
        if type(values["enabled"]) is not bool:
            raise ValueError("watchdog.enabled must be a boolean")
        result["enabled"] = values["enabled"]
    if "check_interval" in values:
        result["check_interval"] = _bounded_int(
            values["check_interval"], "watchdog.check_interval", 10, 3600
        )
    if "levels" in values:
        levels = values["levels"]
        if not isinstance(levels, list) or len(levels) > 3:
            raise ValueError("watchdog.levels must be a list with at most 3 entries")
        normalized = []
        for index, level in enumerate(levels, 1):
            _validate_keys(level, {"after", "subject", "body"}, f"watchdog.levels[{index}]")
            if not isinstance(level.get("subject", ""), str) or not isinstance(level.get("body", ""), str):
                raise ValueError(f"watchdog.levels[{index}] subject and body must be strings")
            normalized.append({
                "after": _bounded_int(level.get("after"), f"watchdog.levels[{index}].after", 10, 86400),
                "subject": level.get("subject", ""),
                "body": level.get("body", ""),
            })
        result["levels"] = normalized
    return result


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
        raw = _yaml.load(f)

    return _resolve_env(raw)


def update_config_yaml(config_path: Path, mutator) -> dict:
    """config.yaml を読み込み、mutatorで書き換えてからアトミック保存する。

    コメント・フォーマットを保持したまま設定値を更新し、
    一時ファイルへの書き込み → os.replace() により、
    書き込み中のクラッシュによるファイル破損を防止する。

    Args:
        config_path: 対象のconfig.yamlパス
        mutator: raw dict を直接書き換える呼び出し可能オブジェクト

    Returns:
        更新後のraw dict
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = _yaml.load(f)
    mutator(raw)

    # アトミック書き込み: 一時ファイル → os.replace()
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            _yaml.dump(raw, f)
        os.replace(temp_path, config_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    return raw

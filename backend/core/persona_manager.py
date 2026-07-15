"""ペルソナ管理。SOUL.md / SKILL.md / style.yaml の読込とシステムプロンプト化。"""

import re
from pathlib import Path
from typing import Optional

import yaml


# ── バリデーション ────────────────────────────────────────────────

_PERSONA_ID_RE = re.compile(r"[a-zA-Z0-9_\-]+")
_MAX_PERSONA_ID_LEN = 64

def validate_persona_id(persona_id: str) -> str:
    """persona_id が安全な文字列か検証し、パストラバーサルを防止する。

    許可: 英数字、アンダースコア、ハイフン。最大64文字。
    拒否時は ValueError を送出。
    """
    if not persona_id or not _PERSONA_ID_RE.fullmatch(persona_id):
        raise ValueError(f"invalid persona_id: {persona_id!r}")
    if len(persona_id) > _MAX_PERSONA_ID_LEN:
        raise ValueError(
            f"persona_id too long: {len(persona_id)} chars (max {_MAX_PERSONA_ID_LEN})"
        )
    return persona_id


# ── style.yaml 読込 ──────────────────────────────────────────────

def load_style_yaml(style_path: Path) -> dict | None:
    """style.yaml を読み込んで返す。存在しない場合は None。"""
    if not style_path.exists():
        return None
    with open(style_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_style_instruction(style: dict) -> str:
    """スタイル辞書からシステムプロンプト用の指示文を生成する。"""
    parts = []

    # 語り手
    if style.get("viewpoint") == "ai_character":
        parts.append("- 語り手: AIキャラクター視点（あなた自身の視点で描写する）")
    elif style.get("viewpoint") == "user_character":
        parts.append("- 語り手: ユーザーキャラクター視点（ユーザーのキャラクターを中心に描写する）")

    # 地の文
    narration = style.get("narration", True)
    if narration:
        parts.append("- 地の文: あり（小説形式で情景描写・心理描写を含める）")
        # 人称（地の文ありの場合のみ有効）
        if style.get("person") == "first":
            parts.append("- 人称: 一人称（地の文は語り手の一人称で統一する）")
        elif style.get("person") == "third":
            parts.append("- 人称: 三人称（地の文はキャラクター名または「彼女」等で記述する）")
    else:
        parts.append("- 地の文: なし（セリフ・会話文のみ。情景描写や心理描写は省略し、テンポ重視の応答を行う）")

    return "## 文体設定（セッション固定・上書き禁止）\n" + "\n".join(parts)


class PersonaManager:
    """複数ペルソナの管理と切替。

    personas/ ディレクトリ配下の各サブディレクトリをペルソナとして認識する。
    各ペルソナは SOUL.md と SKILL.md を持つ。
    style.yaml が存在する場合は文体設定（StyleProfile）も管理する。
    """

    def __init__(self, personas_dir: str | Path, default_persona: str,
                 default_style: dict | None = None):
        self.personas_dir = Path(personas_dir).resolve()
        self.active: Optional[str] = None
        self.default_persona = default_persona
        self.default_style = dict(default_style or {})
        self._locked_style: Optional[dict] = None  # セッション中ロックされたスタイル

    @property
    def active_dir(self) -> Path:
        return self.personas_dir / self.active

    # ── ペルソナ一覧・切替 ────────────────────────────────────────

    def list_personas(self) -> list[dict]:
        """利用可能なペルソナ一覧を返す。更新日時降順。"""
        if not self.personas_dir.exists():
            return []
        result = []
        for d in self.personas_dir.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                soul_path = d / "SOUL.md"
                name = self._extract_name(soul_path) if soul_path.exists() else d.name
                updated = ""
                if soul_path.exists():
                    import time
                    updated = time.strftime("%Y-%m-%d %H:%M", time.localtime(soul_path.stat().st_mtime))
                result.append({"id": d.name, "name": name, "updated": updated})
        result.sort(key=lambda x: x["updated"], reverse=True)
        return result

    def switch(self, persona_id: str):
        """アクティブペルソナを切り替える。スタイルロックは解除される。"""
        from logging import getLogger
        validate_persona_id(persona_id)
        target = self.personas_dir / persona_id
        if not target.exists() or not target.is_dir():
            raise ValueError(f"persona not found: {persona_id}")
        prev = self.active
        self.active = persona_id
        self._locked_style = None  # ペルソナ切替でロック解除
        getLogger("main").debug(
            "persona_manager.switch: %s → %s", prev, persona_id
        )

    def ensure_active(self):
        """アクティブペルソナが未設定ならデフォルトを設定する。"""
        if self.active is None:
            from logging import getLogger
            getLogger("main").warning(
                "persona_manager.ensure_active: active was None! switching to default=%s",
                self.default_persona,
            )
            self.switch(self.default_persona)

    # ── システムプロンプト構築 ────────────────────────────────────

    def get_system_prompt(self) -> list[dict]:
        """現在アクティブなペルソナのシステムプロンプトを返す。

        SOUL.md + SKILL.md に加え、スタイルがロックされている場合は
        文体指示を追加する。

        Returns:
            [{"role": "system", "content": "SOUL.md"}, ...]
        """
        self.ensure_active()
        d = self.active_dir
        messages = []

        soul_path = d / "SOUL.md"
        if soul_path.exists():
            content = soul_path.read_text(encoding="utf-8")
            messages.append({"role": "system", "content": content})

        skill_path = d / "SKILL.md"
        if skill_path.exists():
            content = skill_path.read_text(encoding="utf-8")
            messages.append({"role": "system", "content": content})

        # スタイルロックされている場合、文体指示を追加
        if self._locked_style is not None:
            instruction = build_style_instruction(self._locked_style)
            messages.append({"role": "system", "content": instruction})

        return messages

    # ── StyleProfile ───────────────────────────────────────────────

    def get_default_style(self) -> dict | None:
        """アクティブペルソナの style.yaml を返す。存在しない場合は None。"""
        self.ensure_active()
        return load_style_yaml(self.active_dir / "style.yaml")

    def get_presets(self) -> list[dict]:
        """style.yaml 内の presets を返す。不在時は空リスト。"""
        style = self.get_default_style()
        return style.get("presets", []) if style else []

    def start_session(self, style_override: dict | None = None) -> dict:
        """セッション開始。スタイルをロックし、以後変更不可にする。

        style_override が指定された場合はデフォルトスタイルにマージする。
        style.yaml が存在しない場合は、style_override が必須。

        Returns:
            ロックされたスタイル辞書
        """
        default = self.get_default_style()
        base = dict(self.default_style)
        if default is not None:
            base.update(default.get("style", {}))

        if not base:
            if style_override is None:
                raise ValueError(
                    "style.yaml not found and no style_override provided. "
                    "Use persona_studio to create style.yaml first."
                )
            self._locked_style = style_override
        else:
            self._locked_style = {**base, **(style_override or {})}

        return dict(self._locked_style)

    def get_active_style(self) -> dict | None:
        """現在のセッションでロックされているスタイルを返す。未ロック時は None。"""
        return self._locked_style

    # ── 内部ユーティリティ ────────────────────────────────────────

    @staticmethod
    def _extract_name(soul_path: Path) -> str:
        """SOUL.md の1行目（# SOUL: 名前）からペルソナ名を抽出。"""
        try:
            first_line = soul_path.read_text(encoding="utf-8").split("\n")[0]
            if first_line.startswith("# SOUL:") or first_line.startswith("# SOUL："):
                return first_line.split(":", 1)[1].strip().lstrip("#").strip()
        except Exception:
            pass
        return soul_path.parent.name

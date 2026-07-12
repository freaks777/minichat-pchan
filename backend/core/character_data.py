"""CharacterData — RPキャラクターの内部共通データモデル。

全コンポーネント（Parser / Editor / Generator）はこのデータクラスを介して
キャラクター情報を読み書きする。フォーム・SOUL.md・SKILL.md のいずれにも
依存しない、システム唯一の正（Source of Truth）。
"""

from dataclasses import dataclass, field


@dataclass
class CharacterData:
    """1キャラクター分の全情報。

    fields 属性にフォーム対応の構造化フィールドを持ち、
    extra_sections にフォームに収まらない余剰情報（成人要素・AI指示等）を保持する。
    """

    # ── 基本情報 ──
    persona_id: str = ""
    name: str = ""
    sex: str = ""
    gender: str = ""
    age: str = ""
    birthday: str = ""
    species: str = "人間"
    blood: str = ""

    # ── 身体 ──
    height: str = ""
    weight: str = ""
    bwh: str = ""

    # ── 外見 ──
    hair: str = ""
    eyes: str = ""
    skin: str = ""
    clothing: str = ""

    # ── 人物 ──
    personality: str = ""
    principles: str = ""       # 行動原理・判断基準（v3.3 追加）
    firstperson: str = ""
    secondperson: str = ""
    tone: str = ""
    speech: str = ""           # 口調サンプル（状況別セリフ例）
    likes: str = ""
    habits: str = ""

    # ── 立場 ──
    occupation: str = ""
    skills: str = ""

    # ── その他 ──
    background: str = ""       # 生い立ち・現在の状況・経済感覚を含む
    forbidden: str = ""
    opening_scene: str = ""    # セッション開始時の状況説明

    # ── 余剰データ ──
    extra_sections: list[dict] = field(default_factory=list)
    # [{"title": "成人設定", "content": "..."}, ...]

    # ── メタデータ ──
    extraction_method: str = ""  # "llm" | "direct" — 抽出方法の記録（デバッグ用）

    # ── メソッド ──

    def to_dict(self) -> dict:
        """JSONシリアライズ用の dict に変換。"""
        return {
            "persona_id": self.persona_id,
            "name": self.name,
            "sex": self.sex,
            "gender": self.gender,
            "age": self.age,
            "birthday": self.birthday,
            "species": self.species,
            "blood": self.blood,
            "height": self.height,
            "weight": self.weight,
            "bwh": self.bwh,
            "hair": self.hair,
            "eyes": self.eyes,
            "skin": self.skin,
            "clothing": self.clothing,
            "personality": self.personality,
            "principles": self.principles,
            "firstperson": self.firstperson,
            "secondperson": self.secondperson,
            "tone": self.tone,
            "speech": self.speech,
            "likes": self.likes,
            "habits": self.habits,
            "occupation": self.occupation,
            "skills": self.skills,
            "background": self.background,
            "forbidden": self.forbidden,
            "opening_scene": self.opening_scene,
            "extra_sections": self.extra_sections,
            "extraction_method": self.extraction_method,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterData":
        """dict から CharacterData を復元。未知キーは無視。"""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in d.items() if k in known}
        # extra_sections が文字列で来た場合の防御（list に変換）
        if isinstance(kwargs.get("extra_sections"), str):
            kwargs["extra_sections"] = [{"title": "", "content": kwargs["extra_sections"]}]
        return cls(**kwargs)

    @property
    def field_names(self) -> list[str]:
        """全フィールド名一覧（extra_sections, extraction_method を除く）。"""
        return [
            "persona_id", "name", "sex", "gender", "age", "birthday", "species", "blood",
            "height", "weight", "bwh",
            "hair", "eyes", "skin", "clothing",
            "personality", "principles", "firstperson", "secondperson",
            "tone", "speech", "likes", "habits",
            "occupation", "skills",
            "background", "forbidden", "opening_scene",
        ]

    _DEFAULTS: dict = None  # クラス変数、初回アクセス時に遅延生成

    @classmethod
    def _get_defaults(cls) -> dict:
        """各フィールドのデフォルト値を返す（is_empty 用）。"""
        if cls._DEFAULTS is None:
            dummy = cls()
            cls._DEFAULTS = {f: getattr(dummy, f) for f in cls.__dataclass_fields__}
        return cls._DEFAULTS

    def is_empty(self) -> bool:
        """実質的なデータが何も入っていないか（デフォルト値との比較）。"""
        defaults = self._get_defaults()
        for name in self.field_names:
            if getattr(self, name) != defaults.get(name):
                return False
        return len(self.extra_sections) == 0

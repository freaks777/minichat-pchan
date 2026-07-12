"""persona_studio プラグイン — ペルソナ作成・編集支援。

SOUL.md / SKILL.md / style.yaml の生成、編集、テスト会話を提供する。
hook は持たず、独立した API エンドポイント群として動作する。
"""

from pathlib import Path

from core.api import chat_sync
from core.persona_manager import load_style_yaml, validate_persona_id
from plugins.base import PluginBase


# ── LLM プロンプトテンプレート ──────────────────────────────────

ESTIMATE_STYLE_PROMPT = """以下のキャラクター設定文から、文体設定を推定し、JSON形式でのみ返せ。
説明や前置きは一切不要。以下のJSONオブジェクトだけを返すこと。

{{ "viewpoint": "ai_character" または "user_character",
  "person": "first" または "third",
  "narration": true または false }}

判定基準:
- viewpoint: 設定文の語り手がキャラ自身（「私は」「俺は」）なら "ai_character"。
  語り手が外部の語り手やユーザーキャラなら "user_character"。
- person: 地の文が一人称（「私は〜」）なら "first"、三人称（「彼女は〜」「○○は〜」）なら "third"。
- narration: 情景描写や心理描写を含む小説調なら true、セリフのみのチャット調なら false。

設定文:
{soul_text}"""


TEMPLATE_PROMPT = """あなたはペルソナ設定ファイル作成アシスタントです。
以下のフォーム入力を元に、SOUL.md と SKILL.md を生成してください。

出力は以下のJSON形式のみで返すこと（説明不要）:
{{
  "soul_md": "SOUL.mdの内容（マークダウン）",
  "skill_md": "SKILL.mdの内容（マークダウン）"
}}

SOUL.md には以下のセクションを必ず含めること:

## 基本情報（機械抽出用。必ずキー: 値 形式で記述）
- 身体的性別: {sex}
- 性自認・表現: {gender}
- 年齢: {age}
- 誕生日: {birthday}
- 種族: {species}
- 血液型: {blood}
- 身長: {height}
- 体重: {weight}
- BWH: {bwh}

## 外見（機械抽出用）
- 髪: {hair}
- 目: {eyes}
- 肌: {skin}
- 服装: {clothing}

## 人格定義
{personality}
- 行動原理: {principles}
- 一人称: {firstperson}
- 二人称: {secondperson}

## 口調
{tone}

## 口調サンプル
（キャラクターの代表的なセリフ例を場面別に3〜5個）

## 好き嫌い
{likes}

## 癖・習慣
{habits}

## 立場
- 職業/所属: {occupation}
- 特殊能力/スキル: {skills}

## 背景
{background}

## 禁止事項
{forbidden}

## 開始時の状況（セッション開始時に表示される場面説明）
{opening_scene}

文体設定:
- 語り手: {viewpoint_label}
- 人称: {person_label}
- 地の文: {narration_label}

補足情報の扱い:
以下の補足情報を、SOUL.md 全体へ自然に統合してください。
- 削除禁止。情報を捨ててはいけない
- 本文の適切な場所（外見・人物・背景等）へ自然に溶け込ませること
- どうしても統合できない情報のみ、SOUL.md 末尾に「## 補足情報」として残すこと

{extra_sections_text}

SKILL.md には以下を含めること:
- このキャラクターのRP補足ルール、特殊状況対応
- フロントマター（YAML）: name, description, version: 1.0.0, always_load: true"""


# ── フィールド抽出プロンプト（v3.3: CharacterData 中心設計） ──

# LLMが抽出すべき全フィールド一覧（description はLLMへの指示用）
_EXTRACTION_FIELDS = [
    ("name", "キャラクターの名前"),
    ("sex", "身体的性別（男性/女性）"),
    ("gender", "性自認・表現（男の娘/男装/中性 等）"),
    ("age", "年齢（21歳/20代 等）"),
    ("birthday", "誕生日（7月7日 等）"),
    ("species", "種族（人間/エルフ/獣人/犬/妖怪 等）"),
    ("blood", "血液型（A/B/O/AB）"),
    ("height", "身長（170cm 等）"),
    ("weight", "体重（55kg 等）"),
    ("bwh", "スリーサイズ（B86 W60 H85 等）"),
    ("hair", "髪の色・長さ・特徴"),
    ("eyes", "目の色・形・印象"),
    ("skin", "肌の色・質感"),
    ("clothing", "服装のスタイル・傾向"),
    ("personality", "性格。その人がどんな人物か（気質・雰囲気・感情の出方）。例: 「冷静で感情を表に出さないが、内面には音楽への情熱がある」"),
    ("principles", "行動原理・判断基準・信条。行動を選ぶときのルール。例: 「暴力より心理戦を選ぶ」「約束は必ず守る」。性格（personality）とは別。"),
    ("firstperson", "一人称（私/俺/僕/自分 等）"),
    ("secondperson", "二人称（君/あなた/お前 等）。相手ごとに呼び方を変える場合はそれも含める"),
    ("tone", "口調の特徴（声色・語尾・話し方の傾向）"),
    ("speech", "口調サンプル。状況別のセリフ例（驚いた時/怒っている時/笑い方 等のラベル付きで）"),
    ("likes", "好き嫌い（嗜好・苦手なもの）"),
    ("habits", "癖・習慣・無意識の行動パターン"),
    ("occupation", "職業・所属（DJ/学生/冒険者 等）"),
    ("skills", "特殊能力・スキル（観察力/魔法/剣術 等）"),
    ("background", "背景。生い立ち・現在の状況・生活水準・経済感覚を含む"),
    ("forbidden", "禁止事項。使ってはいけない語尾・禁止行動"),
    ("opening_scene", "セッション開始時の状況説明。空欄なら自動生成。例: 事務所。夕方。鏡花は窓際のソファで紅茶を飲んでいる。"),
]

EXTRACT_FIELDS_PROMPT = """あなたは情報抽出エンジンです。以下のテキストからキャラクター情報を抽出してください。

【最重要ルール】
- 要約禁止。情報を削除・短縮・言い換えしてはいけない
- 元テキストの意味を変えてはいけない
- 情報が見つからない項目は空文字列 "" にする

【出力形式】
以下のJSON形式のみで返すこと（説明や前置きは一切不要）:
{{
  "fields": {{
    "name": "...",
    "sex": "...",
    （全{field_count}項目）
  }},
  "extra_sections": [
    {{"title": "セクション名", "content": "この項目に分類できなかった全文"}}
  ]
}}

【各フィールドの説明】
{field_descriptions}

【分類の指針】
- 元テキストの内容を、意味的に最も近いフィールドに全文を入れる
- 一つの情報が複数フィールドに跨る場合は適切に分割して各フィールドに配置する
- どのフィールドにも該当しない情報は extra_sections に入れる
- extra_sections の title は元テキストの見出しを可能な限り保持する

【抽出元テキスト】
{raw_text}"""

# ── 旧プロンプト（互換用、非推奨） ──

FREETEXT_PROMPT = """あなたはペルソナ設定ファイル作成アシスタントです。
以下の文章を、SOUL.md と SKILL.md の形式に変換してください。

出力は以下のJSON形式のみで返すこと（説明不要）:
{{
  "soul_md": "SOUL.mdの内容（マークダウン）",
  "skill_md": "SKILL.mdの内容（マークダウン）"
}}

SOUL.md には以下を含めること:
- キャラクター名、人格定義、外見、背景、口調サンプル、禁止事項
- 語り手・人称・地の文に関する指示（後述のstyle情報を反映）

SKILL.md には以下を含めること:
- RP補足ルール、特殊状況対応
- フロントマター（YAML）: name, description, version: 1.0.0, always_load: true

文体設定（SOUL.md内の描写指示に反映すること）:
- 語り手: {viewpoint_label}
- 人称: {person_label}
- 地の文: {narration_label}

変換元の文章:
{raw_text}"""


REFINE_PROMPT = """以下のペルソナ設定（SOUL.md + SKILL.md）を、指示に従って部分修正してください。

出力は以下のJSON形式のみで返すこと（説明不要）:
{{
  "soul_md": "修正後のSOUL.md（マークダウン）",
  "skill_md": "修正後のSKILL.md（マークダウン）"
}}

現在の設定:
SOUL.md:
{soul_md}

SKILL.md:
{skill_md}

修正指示:
{instruction}"""


TEST_CHAT_PROMPT = """あなたは以下のキャラクター設定に従って応答してください。
これはテスト会話です。簡潔にキャラクターらしく振る舞ってください。

{soul_md}

{skill_md}

ユーザー: {message}"""


# ── ユーティリティ ──────────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """LLM応答からJSONを抽出する。コードブロックや余計な前置きを除去。"""
    import json
    import re

    original = text
    text = text.strip()
    # ```json ... ``` を抽出
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    # 先頭の { から末尾の } まで（最も外側のJSONオブジェクト）
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 切り捨てられたJSONを修復（閉じ括弧を補完）
        if text.count("{") > text.count("}"):
            text = text + "}" * (text.count("{") - text.count("}"))
        if text.count("[") > text.count("]"):
            text = text + "]" * (text.count("[") - text.count("]"))
        # 文字列の引用符も補完
        if text.count('"') % 2 != 0:
            text = text + '"'
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"JSON parse failed: {e}\n"
                f"Raw response (first 500 chars): {original[:500]}"
            )


def _extract_name_from_soul(soul_md: str) -> str:
    """SOUL.mdからペルソナ名を抽出。"""
    import re
    m = re.search(r"#\s*SOUL\s*:\s*(.+?)(?:\s*—|$)", soul_md)
    if m:
        return m.group(1).strip()
    # なければ1行目の見出しから
    first = soul_md.strip().split("\n")[0]
    return first.lstrip("#").strip()


# ── プラグインクラス ────────────────────────────────────────────

class PersonaStudioPlugin(PluginBase):
    name = "persona_studio"
    hooks = []  # hook 不要。独立APIとして動作

    def __init__(self):
        self._config = None

    def configure(self, config: dict):
        """API呼び出し用の設定を受け取る。main.py の起動時に呼ばれる。"""
        self._config = config

    def _make_config(self, max_tokens: int = 2000) -> dict:
        """max_tokens を上書きした設定のコピーを返す。"""
        import copy
        c = copy.deepcopy(self._config)
        c["api"]["max_tokens"] = max_tokens
        return c

    async def run(self, hook: str, data, ctx: dict):
        # 本プラグインはhookを使用しない
        return None

    # ── スタイル推定 ──────────────────────────────────────────

    async def estimate_style_from_soul(self, soul_md_text: str) -> dict:
        """SOUL.mdの自然言語記述から style を推定する。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")
        config = self._make_config(max_tokens=500)
        prompt = ESTIMATE_STYLE_PROMPT.format(soul_text=soul_md_text[:4000])
        messages = [{"role": "user", "content": prompt}]
        result = await chat_sync(messages, config)
        return _parse_json_response(result)

    # ── テンプレートフォーム → SOUL/SKILL ────────────────────

    async def create_via_template(self, form_data: dict) -> dict:
        """フォーム入力値から SOUL.md / SKILL.md を生成。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        config = self._make_config(max_tokens=16000)
        f = form_data.get("fields", form_data)  # fields オブジェクトまたは旧形式互換
        style = form_data.get("style_override", {})
        extra = form_data.get("extra_sections", [])
        prompt = TEMPLATE_PROMPT.format(
            name=f.get("name", "未設定"),
            sex=f.get("sex", ""),
            gender=f.get("gender", ""),
            age=f.get("age", ""),
            birthday=f.get("birthday", ""),
            species=f.get("species", "人間"),
            blood=f.get("blood", ""),
            height=f.get("height", ""),
            weight=f.get("weight", ""),
            bwh=f.get("bwh", ""),
            hair=f.get("hair", ""),
            eyes=f.get("eyes", ""),
            skin=f.get("skin", ""),
            clothing=f.get("clothing", ""),
            personality=f.get("personality", "未設定"),
            principles=f.get("principles", ""),
            firstperson=f.get("firstperson", ""),
            secondperson=f.get("secondperson", ""),
            tone=f.get("tone", "未設定"),
            likes=f.get("likes", ""),
            habits=f.get("habits", ""),
            occupation=f.get("occupation", ""),
            skills=f.get("skills", ""),
            background=f.get("background", "未設定"),
            forbidden=f.get("forbidden", "特になし"),
            opening_scene=_opening_scene_prompt(f.get("opening_scene", "")),
            viewpoint_label=_viewpoint_label(style.get("viewpoint", "ai_character")),
            person_label=_person_label(style.get("person", "first")),
            narration_label="あり（小説調）" if style.get("narration", True) else "なし（チャット調）",
            extra_sections_text=_format_extra_sections(extra),
        )
        messages = [{"role": "user", "content": prompt}]
        result = await chat_sync(messages, config)
        if not result or not result.strip():
            raise ValueError("APIが空の応答を返しました。モデルの制限の可能性があります。")
        parsed = _parse_json_response(result)
        return {
            "soul_md": parsed.get("soul_md", ""),
            "skill_md": parsed.get("skill_md", ""),
            "persona_id": f.get("name", "unknown"),
            "style": {
                "viewpoint": style.get("viewpoint", "ai_character"),
                "person": style.get("person", "first"),
                "narration": style.get("narration", True),
            },
        }

    # ── フィールド抽出（v3.3: LLMで構造化JSON抽出） ──

    async def extract_fields(self, raw_text: str) -> dict:
        """自由記述テキストから CharacterData のフィールドを抽出。

        LLMにSOUL.mdを書かせるのではなく、構造化JSONでフィールド値を直接返させる。
        フォームに収まらない情報は extra_sections として保持。
        """
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        config = self._make_config(max_tokens=16000)
        prompt = _build_extraction_prompt(raw_text)
        messages = [{"role": "user", "content": prompt}]
        result = await chat_sync(messages, config)

        if not result or not result.strip():
            raise ValueError("APIが空の応答を返しました。テキストが長すぎるか、モデルの制限の可能性があります。")

        parsed = _parse_json_response(result)
        fields = parsed.get("fields", {})
        extra = parsed.get("extra_sections", [])

        # extra_sections が文字列で来た場合の防御
        if isinstance(extra, str):
            extra = [{"title": "", "content": extra}]

        return {
            "fields": fields,
            "extra_sections": extra,
            "extraction_method": "llm",
        }

    # ── フリーテキスト変換（旧方式、非推奨） ──

    async def convert_freetext(
        self, raw_text: str, style_override: dict | None = None
    ) -> dict:
        """自由記述テキストを SOUL.md / SKILL.md に変換。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        config = self._make_config(max_tokens=16000)
        style = style_override or {"viewpoint": "ai_character", "person": "third", "narration": True}
        prompt = FREETEXT_PROMPT.format(
            raw_text=raw_text[:6000],
            viewpoint_label=_viewpoint_label(style.get("viewpoint", "ai_character")),
            person_label=_person_label(style.get("person", "third")),
            narration_label="あり（小説調）" if style.get("narration", True) else "なし（チャット調）",
        )
        messages = [{"role": "user", "content": prompt}]
        result = await chat_sync(messages, config)
        if not result or not result.strip():
            raise ValueError("APIが空の応答を返しました。テキストが長すぎるか、モデルの制限の可能性があります。")
        parsed = _parse_json_response(result)
        return {
            "soul_md": parsed.get("soul_md", ""),
            "skill_md": parsed.get("skill_md", ""),
            "style": style,
        }

    # ── 部分修正 ──────────────────────────────────────────────

    async def refine(self, draft: dict, instruction: str) -> dict:
        """生成済みドラフトをLLMとの対話で部分修正。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        config = self._make_config(max_tokens=6000)
        prompt = REFINE_PROMPT.format(
            soul_md=draft.get("soul_md", "")[:2000],
            skill_md=draft.get("skill_md", "")[:1500],
            instruction=instruction,
        )
        messages = [{"role": "user", "content": prompt}]
        result = await chat_sync(messages, config)
        parsed = _parse_json_response(result)
        return {
            "soul_md": parsed.get("soul_md", draft.get("soul_md", "")),
            "skill_md": parsed.get("skill_md", draft.get("skill_md", "")),
            "style": draft.get("style", {}),
        }

    # ── テスト会話 ────────────────────────────────────────────

    async def test_chat(self, draft: dict, message: str) -> str:
        """ドラフトのSOUL/SKILLで使い捨てのテスト会話を行う。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        config = self._make_config(max_tokens=500)
        prompt = TEST_CHAT_PROMPT.format(
            soul_md=draft.get("soul_md", ""),
            skill_md=draft.get("skill_md", ""),
            message=message,
        )
        messages = [{"role": "user", "content": prompt}]
        return await chat_sync(messages, config)

    # ── 保存 ──────────────────────────────────────────────────

    def save(self, personas_dir: Path, persona_id: str, draft: dict):
        """ドラフトを personas/{persona_id}/ に保存する。"""
        validate_persona_id(persona_id)
        target = Path(personas_dir) / persona_id
        target.mkdir(parents=True, exist_ok=True)

        if draft.get("soul_md"):
            (target / "SOUL.md").write_text(draft["soul_md"], encoding="utf-8")

        if draft.get("skill_md"):
            (target / "SKILL.md").write_text(draft["skill_md"], encoding="utf-8")

        if draft.get("style"):
            _write_style_yaml(target / "style.yaml", draft["style"])


# ── 内部ヘルパー ────────────────────────────────────────────────

def _opening_scene_prompt(user_input: str) -> str:
    """開始時の状況のプロンプト指示を生成。"""
    if not user_input.strip():
        return (
            "（ユーザー未指定。"
            "以下のキャラクター設定と世界観から、セッション開始時に表示する状況説明を生成してください。"
            "場所・時間・登場人物の位置関係・周囲の様子・雰囲気・身体の状態などを含めること。"
            "AIキャラがユーザーキャラと同じ場所にいない場合は、それぞれの現在地と遭遇の見込みも説明すること）"
        )
    return (
        f"{user_input}\n\n"
        "（上記はユーザー指定の開始状況です。"
        "不足している要素（場所・時間・登場人物の位置関係・周囲の様子・雰囲気・身体の状態・"
        "AIキャラの所在と遭遇見込みなど）があれば補足してください。"
        "内容が十分であれば、そのまま使用してください）"
    )


def _viewpoint_label(v: str) -> str:
    return "AIキャラクター視点（キャラ自身の視点）" if v == "ai_character" else "ユーザーキャラクター視点"


def _person_label(p: str) -> str:
    return "一人称（「私は」「俺は」等）" if p == "first" else "三人称（「○○は」「彼女は」等）"


def _build_extraction_prompt(raw_text: str) -> str:
    """フィールド抽出用プロンプトを構築。"""
    descriptions = "\n".join(
        f"- {field}: {desc}" for field, desc in _EXTRACTION_FIELDS
    )
    return EXTRACT_FIELDS_PROMPT.format(
        field_count=len(_EXTRACTION_FIELDS),
        field_descriptions=descriptions,
        raw_text=raw_text,
    )


def _format_extra_sections(sections: list[dict]) -> str:
    """extra_sections をプロンプト用テキストに整形。無題セクションには連番を振る。"""
    if not sections:
        return "（なし）"
    untitled_count = 0
    result = []
    for s in sections:
        title = (s.get("title") or "").strip()
        content = (s.get("content") or "").strip()
        if not content:
            continue
        if not title:
            untitled_count += 1
            title = f"その他{untitled_count}" if untitled_count > 1 else "その他"
        else:
            # タイトルがあり、かつ無題が既に1つだけ出ている場合は連番にしない
            pass
        result.append(f"### {title}\n{content}")
    if not result:
        return "（なし）"
    return "\n\n".join(result)


def _write_style_yaml(path: Path, style: dict):
    """style.yaml を書き出す。"""
    import yaml
    data = {
        "style": {
            "viewpoint": style.get("viewpoint", "ai_character"),
            "person": style.get("person", "first"),
            "narration": style.get("narration", True),
        },
        "presets": [
            {
                "id": "novel_ai",
                "label": "小説調（AI視点・一人称）",
                "style": {"viewpoint": "ai_character", "person": "first", "narration": True},
            },
            {
                "id": "novel_user",
                "label": "小説調・ユーザー視点（三人称）",
                "style": {"viewpoint": "user_character", "person": "third", "narration": True},
            },
            {
                "id": "chat",
                "label": "チャット調（地の文なし）",
                "style": {"viewpoint": "ai_character", "person": "first", "narration": False},
            },
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

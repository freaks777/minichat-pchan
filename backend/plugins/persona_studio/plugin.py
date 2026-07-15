"""persona_studio プラグイン — ペルソナ作成・編集支援。

SOUL.md / SKILL.md / style.yaml の生成、編集、テスト会話を提供する。
hook は持たず、独立した API エンドポイント群として動作する。
"""

import logging
import asyncio
import re
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
    ("principles", "行動原理・判断基準・信条。行動を選ぶときのルールや価値観。例: 「暴力より心理戦を選ぶ」「約束は必ず守る」「仲間を何より優先する」。性格（personality）とは別だが、明示的な記述がない場合は、禁止事項や性格記述から推測できる行動規範を抽出してもよい"),
    ("firstperson", "一人称（私/俺/僕/自分 等）"),
    ("secondperson", "二人称（君/あなた/お前 等）。相手ごとに呼び方を変える場合はそれも含める"),
    ("tone", "口調の特徴（声色・語尾・話し方の傾向）"),
    ("speech", "口調サンプル。状況別のセリフ例（驚いた時/怒っている時/笑い方 等のラベル付きで）"),
    ("likes", "好き嫌い（嗜好・苦手なもの）"),
    ("habits", "癖・習慣・無意識の行動パターン（一人称・二人称・口調は含めない。それらは専用フィールドがある）"),
    ("occupation", "職業・所属（DJ/学生/冒険者 等）"),
    ("skills", "特殊能力・スキル（観察力/魔法/剣術 等）"),
    ("background", "背景。生い立ち・現在の状況・生活水準・経済感覚を含む"),
    ("forbidden", "禁止事項。使ってはいけない語尾・禁止行動"),
    ("opening_scene", "セッション開始時の状況説明。空欄なら自動生成。例: 事務所。夕方。鏡花は窓際のソファで紅茶を飲んでいる。"),
]

# 1バッチあたりの抽出フィールド数（無料枠の出力制限対策）
_EXTRACTION_BATCH_SIZE = 10

# ── 前処理: Markdown除去 + 機械的フィールド抽出 ─────────────────

# 機械抽出パターン (field_name, regex)
# 単純な1行フィールドのみ。複数行・複雑な内容（firstperson等）はLLMに任せる
_PREPROCESS_PATTERNS = [
    ("name",     r"(?:名前|氏名|キャラクター名)\s*[：:\s]+(.+?)(?:\n|$)"),
    ("sex",      r"(?:性別|身体的性別)\s*[：:\s]+(.+?)(?:\n|$)"),
    ("age",      r"年齢\s*[：:\s]*(\d+\s*歳)"),
    ("birthday", r"誕生日\s*[：:\s]*(\d+月\d+日)"),
    ("blood",    r"血液型\s*[：:\s]*([ABO]{1,2}型)"),
    ("height",   r"身長\s*[：:\s]*(\d+\s*cm)"),
    ("weight",   r"体重\s*[：:\s]*(\d+\s*kg)"),
]


def _preprocess_text(raw_text: str) -> tuple[str, dict]:
    """テキストの前処理: Markdown除去 + 機械的フィールド抽出。

    Returns:
        (cleaned_text, extracted_fields): 整形済みテキストと機械抽出結果
    """
    text = raw_text

    # 1. Markdown 構造記号の除去
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # 見出し
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)                 # 太字
    text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)  # 箇条書き記号

    # テーブル記法の簡易変換: | key | value | → key: value
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        s = line.strip()
        if s.startswith('|') and not s.startswith('|-') and not s.startswith('|--'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) >= 2:
                cleaned.append(f"{cells[0]}: {' '.join(cells[1:])}")
                continue
        cleaned.append(line)
    text = '\n'.join(cleaned)

    # 2. 機械的フィールド抽出
    extracted = {}
    for field_name, pattern in _PREPROCESS_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                extracted[field_name] = value

    return text, extracted


# ── LLM プロンプト ──────────────────────────────────────────────

EXTRACT_FIELDS_PROMPT = """あなたは情報抽出エンジンです。以下のテキストからキャラクター情報を抽出してください。

【最重要ルール】
- 情報の内容を削除・短縮・言い換えしてはいけない。元の意味・ニュアンスを保ったまま抽出する
- 情報が見つからない項目は空文字列 "" にする
- テキストは前処理済み（Markdown記号除去済み）のため、構造記号の心配は不要

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
- 元テキストの内容を、意味的に最も近いフィールドに配置する
- 一つの情報が複数フィールドに跨る場合は適切に分割する
- どのフィールドにも該当しない情報のみ extra_sections に入れる
- **すでに fields に分類した情報は extra_sections に重複させないこと**
- extra_sections の title は元テキストの見出しを可能な限り保持する
- 注意: このバッチには表示されているフィールドのみ抽出すること。他のフィールド（一人称/二人称/口調/性格 等）は別バッチで処理されるため、本バッチでは対象フィールドに該当する情報だけを抽出する

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
        self._cancel_event = None  # asyncio.Event（main.py から注入）

    def configure(self, config: dict):
        """API呼び出し用の設定を受け取る。main.py の起動時に呼ばれる。"""
        self._config = config

    def set_cancel_event(self, event):
        """キャンセル用 asyncio.Event を受け取る。"""
        self._cancel_event = event

    def _make_config(self, max_tokens: int = 2000, timeout: int | None = None,
                     provider: str | None = None, model: str | None = None) -> dict:
        """max_tokens / timeout / provider / model を上書きした設定のコピーを返す。"""
        import copy
        c = copy.deepcopy(self._config)
        c["api"]["max_tokens"] = max_tokens
        if timeout is not None:
            c["api"]["timeout"] = timeout
        if provider is not None:
            c["active_provider"] = provider
        if model is not None:
            c["active_model"] = model
        return c

    def _get_fallback_chain(self) -> list[dict]:
        """config.yaml の extraction.fallback_chain を取得。

        未設定または空の場合は active_provider/active_model の単一エントリを返す。
        """
        extraction_cfg = self._config.get("extraction", {})
        chain = extraction_cfg.get("fallback_chain", [])
        if not chain:
            chain = [{
                "provider": self._config.get("active_provider", ""),
                "model": self._config.get("active_model", ""),
            }]
        return chain

    async def _try_with_fallback(
        self, messages: list[dict],
        max_tokens: int = 16000, timeout: int | None = None,
        task_name: str = "extraction",
    ) -> str:
        """フォールバックチェーンを順に試行し、content が非空の最初の結果を返す。

        全滅時は ValueError。
        """
        chain = self._get_fallback_chain()
        logger = logging.getLogger("rp-standalone")

        last_error = None
        for idx, entry in enumerate(chain):
            if self._cancel_event and self._cancel_event.is_set():
                raise asyncio.CancelledError("抽出が中断されました")
            stage_label = f"[{idx+1}/{len(chain)}] {entry['provider']}/{entry['model']}"
            config = self._make_config(
                max_tokens=max_tokens, timeout=timeout,
                provider=entry["provider"], model=entry["model"],
            )
            try:
                result = await chat_sync(messages, config)
                if result and result.strip():
                    logger.info(
                        "%s fallback OK: %s (%d chars)",
                        task_name, stage_label, len(result),
                    )
                    return result
                else:
                    logger.warning("%s fallback empty: %s", task_name, stage_label)
            except Exception as e:
                logger.warning("%s fallback error: %s — %s", task_name, stage_label, e)
                last_error = e
                continue

        raise ValueError(
            f"{task_name}: すべてのフォールバックモデルで失敗しました。"
            f" chain={chain}, last_error={last_error}"
        )

    async def _try_extraction_chain(
        self, messages: list[dict],
        max_tokens: int = 16000, timeout: int | None = None,
        task_name: str = "extraction",
    ) -> dict:
        """抽出用フォールバック: content 非空 + JSONパース可能な最初の結果を返す。

        全滅時は ValueError。
        """
        chain = self._get_fallback_chain()
        logger = logging.getLogger("rp-standalone")

        last_error = None
        for idx, entry in enumerate(chain):
            if self._cancel_event and self._cancel_event.is_set():
                raise asyncio.CancelledError("抽出が中断されました")
            stage_label = f"[{idx+1}/{len(chain)}] {entry['provider']}/{entry['model']}"
            config = self._make_config(
                max_tokens=max_tokens, timeout=timeout,
                provider=entry["provider"], model=entry["model"],
            )
            try:
                raw = await chat_sync(messages, config)
                if not raw or not raw.strip():
                    logger.warning("%s empty: %s", task_name, stage_label)
                    continue
                parsed = _parse_json_response(raw)
                logger.info(
                    "%s OK: %s (%d chars, %d fields)",
                    task_name, stage_label, len(raw),
                    len(parsed.get("fields", {})),
                )
                return parsed
            except ValueError as e:
                logger.warning("%s JSON parse failed: %s — %s", task_name, stage_label, e)
                last_error = e
                continue
            except Exception as e:
                logger.warning("%s error: %s — %s", task_name, stage_label, e)
                last_error = e
                continue

        raise ValueError(
            f"{task_name}: すべてのフォールバックモデルで失敗しました。"
            f" chain={chain}, last_error={last_error}"
        )

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
        result = await self._try_with_fallback(
            messages, max_tokens=16000, task_name="create_via_template",
        )
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

        1. 前処理: Markdown除去 + 機械的フィールド抽出（正規表現）
        2. LLM抽出: 残りのフィールドをバッチ分割し、フォールバックチェーンで試行
        3. マージ: 機械抽出を優先
        """
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        # 入力が長すぎるとプロンプトが膨らみ出力トークン不足になるためトリム
        text = raw_text if len(raw_text) <= 6000 else raw_text[:6000]

        # 1. 前処理: Markdown除去 + 機械的フィールド抽出
        text, mechanical = _preprocess_text(text)
        mech_keys = set(mechanical.keys())
        logger = logging.getLogger("rp-standalone")
        if mech_keys:
            logger.info(
                "extract_fields mechanical: %d fields extracted (%s)",
                len(mech_keys), ', '.join(sorted(mech_keys)),
            )

        # 2. LLM抽出: 機械抽出済みフィールドを除外してバッチ分割
        all_fields = [
            (name, desc) for name, desc in _EXTRACTION_FIELDS
            if name not in mech_keys
        ]
        batches = [
            all_fields[i:i + _EXTRACTION_BATCH_SIZE]
            for i in range(0, len(all_fields), _EXTRACTION_BATCH_SIZE)
        ]

        if not batches:
            # 全フィールドが機械抽出された場合
            logger.info("extract_fields: all fields mechanically extracted, skipping LLM")
            return {
                "fields": mechanical,
                "extra_sections": [],
                "extraction_method": "mechanical",
                "batches": 0,
            }

        logger.info(
            "extract_fields LLM: %d fields in %d batches (mechanical: %d fields)",
            len(all_fields), len(batches), len(mech_keys),
        )

        all_fields_result = {}
        all_extra = []

        for bi, batch_fields in enumerate(batches):
            if self._cancel_event and self._cancel_event.is_set():
                raise asyncio.CancelledError("抽出が中断されました")
            prompt = _build_extraction_prompt(text, batch_fields)
            messages = [{"role": "user", "content": prompt}]
            logger.info(
                "extract_fields batch %d/%d: %d fields, prompt=%d chars",
                bi + 1, len(batches), len(batch_fields), len(prompt),
            )

            # フォールバックチェーンで試行（content非空 + JSONパース可能なモデルを自動選択）
            task_name = f"extract_fields batch {bi+1}/{len(batches)}"
            parsed = await self._try_extraction_chain(
                messages, max_tokens=16000, timeout=300, task_name=task_name,
            )

            fields = parsed.get("fields", {})
            extra = parsed.get("extra_sections", [])

            filled = sum(1 for v in fields.values() if v and str(v).strip())
            logger.info(
                "extract_fields batch %d/%d done: filled=%d/%d fields",
                bi + 1, len(batches), filled, len(batch_fields),
            )

            if isinstance(extra, str):
                extra = [{"title": "", "content": extra}]

            all_fields_result.update(fields)
            # extra_sections は最初のバッチだけ収集（重複防止）
            if bi == 0:
                all_extra = extra

            # バッチ間に短い待機（プロバイダ切替でレート制限は緩和されるが念のため）
            if bi < len(batches) - 1:
                await asyncio.sleep(3)

        # 3. マージ: 機械抽出を優先（LLM結果を上書き）
        for k, v in mechanical.items():
            all_fields_result[k] = v

        # デバッグ: 抽出結果のフィールド→値マッピングをログ出力
        all_field_names = [name for name, _ in _EXTRACTION_FIELDS]
        filled_fields = {k: v for k, v in all_fields_result.items() if v and str(v).strip()}
        empty_fields = [k for k in all_field_names if k not in filled_fields]
        logger.info(
            "extract_fields result: filled=%d/%d fields, empty=%s, extra_sections=%d",
            len(filled_fields), len(all_field_names),
            empty_fields, len(all_extra),
        )
        for k, v in filled_fields.items():
            logger.info("  [%s] %s", k, str(v)[:120])

        return {
            "fields": all_fields_result,
            "extra_sections": all_extra,
            "extraction_method": "llm_batched",
            "batches": len(batches),
        }

    # ── ドラフト保存/読込 ──

    async def save_draft(self, persona_id: str, data: dict) -> dict:
        """フォーム状態をドラフトとして保存。
        Returns: {"status": "ok", "existing_persona": bool}
        """
        import json
        validate_persona_id(persona_id)
        draft_dir = Path(self._config.get("data_dir", "data")) / "drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_path = draft_dir / f"{persona_id}.json"
        draft_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        logging.getLogger("rp-standalone").info("draft saved: %s", persona_id)

        # 既存ペルソナの有無をチェック
        personas_dir = Path(self._config.get("personas_dir", "../personas"))
        existing = (personas_dir / persona_id).is_dir()
        return {"status": "ok", "existing_persona": existing}

    async def load_draft(self, persona_id: str) -> dict | None:
        """保存済みドラフトを読み込む。"""
        import json
        validate_persona_id(persona_id)
        draft_path = Path(self._config.get("data_dir", "data")) / "drafts" / f"{persona_id}.json"
        if not draft_path.exists():
            return None
        logging.getLogger("rp-standalone").info("draft loaded: %s", persona_id)
        return json.loads(draft_path.read_text(encoding="utf-8"))

    async def delete_draft(self, persona_id: str) -> bool:
        """ドラフトを削除。"""
        validate_persona_id(persona_id)
        draft_path = Path(self._config.get("data_dir", "data")) / "drafts" / f"{persona_id}.json"
        if draft_path.exists():
            draft_path.unlink()
            logging.getLogger("rp-standalone").info("draft deleted: %s", persona_id)
            return True
        return False

    # ── フリーテキスト変換（旧方式、非推奨） ──

    async def convert_freetext(
        self, raw_text: str, style_override: dict | None = None
    ) -> dict:
        """自由記述テキストを SOUL.md / SKILL.md に変換。"""
        if not self._config:
            raise RuntimeError("persona_studio not configured")

        style = style_override or {"viewpoint": "ai_character", "person": "third", "narration": True}
        prompt = FREETEXT_PROMPT.format(
            raw_text=raw_text[:6000],
            viewpoint_label=_viewpoint_label(style.get("viewpoint", "ai_character")),
            person_label=_person_label(style.get("person", "third")),
            narration_label="あり（小説調）" if style.get("narration", True) else "なし（チャット調）",
        )
        messages = [{"role": "user", "content": prompt}]
        result = await self._try_with_fallback(
            messages, max_tokens=16000, task_name="convert_freetext",
        )
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


def _build_extraction_prompt(raw_text: str, fields: list | None = None) -> str:
    """フィールド抽出用プロンプトを構築。fields指定時はそのサブセットのみ。"""
    if fields is None:
        fields = _EXTRACTION_FIELDS
    descriptions = "\n".join(
        f"- {field}: {desc}" for field, desc in fields
    )
    return EXTRACT_FIELDS_PROMPT.format(
        field_count=len(fields),
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

# CHANGELOG — RPスタンドアロンアプリ

> 現行仕様は `RPスタンドアロンアプリ_設計書.md` を参照してください。
> 未実装・保留項目は `backlog.md` を参照してください。
>
> **Version記録方針**: 現行versionはv3.11です。本書に独立章が存在するversionだけを記録済みreleaseとして扱い、過去の同一version見出しへ推測で新番号を付与しません。次回version更新は独立章として記録します。

---

## 11. v3.1 追加仕様（2026-07-11）

### 11.1 セッション管理

**セッションID**: 全セッションに8桁の一意識別子を自動発行（`HHMMSS` + 乱数2桁）。ファイル名は `YYYY-MM-DD_HHMMSSRR.jsonl` 形式。旧形式（`YYYY-MM-DD.jsonl`）も互換維持。

**セッション開始フロー**:
```
/setup → キャラ選択 → 文体選択 → POST /api/session/start {persona_id, style_override}
  → persona_manager.switch() → history 空初期化 → session_id 生成
  → .current-session 保存 → redirect /chat
```

**セッション再開** (`/api/session/resume`):
- `session_id` で対象 JSONL を特定 → `history._load_specific()` で読込
- ペルソナ切替 + スタイルロック + `.current-session` 保存

**セッション一覧**:
- 全 `sessions/{persona_id}/*.jsonl` をスキャン
- `.current-session` が存在すれば先頭に追加
- セッションID付きで表示

**セッション削除**:
- JSONLファイルがあれば削除
- なければ `.current-session` を照合（persona_id + session_id）
- どちらもなければゾンビエントリとして成功扱い

### 11.2 履歴編集

**編集API**:
| エンドポイント | 機能 |
|---|---|
| `POST /api/session/update-message` | 指定indexのメッセージ内容更新 + JSONL全書込 |
| `POST /api/session/delete-message` | 指定indexのメッセージ削除。ユーザー発言の場合、後続のAI応答も連動削除 |
| `POST /api/session/truncate` | 指定index以降の全メッセージを削除（再生成用） |

**編集UI**:
- 履歴メッセージはセッション開始時に REST API で取得
- 各メッセージに `[編集] [再生成(ユーザーのみ)] [削除]` ボタン
- ユーザー発言編集 → truncate + 編集後テキストを再送信（`resend` フラグで重複追加防止）
- AI応答編集 → `[編集済]` ラベル付与、正式履歴として扱う
- ダブルクリックでなくボタンクリックによる編集起動、テキストエリアは 70ch 幅

### 11.3 応答中画面移動の保護

SSE切断（応答ストリーミング中に画面移動）をサーバー側で検知（v3.9 で `_run_with_disconnect_guard()` 追加）。受信済みテキストに `[中断]` を付与してJSONLに保存。ユーザー発言は失われない。

### 11.4 watchdog デフォルト OFF

`config.yaml` の `watchdog.enabled` をデフォルト `false` に変更。無効時は監視ループ・エスカレーション文面生成の両方をスキップ。

### 11.5 チャット入力

> **v3.7 で廃止**: 送信キー設定（`session.send_key`）は削除され、常に「Enter で送信、Shift+Enter で改行」に固定された。以下の記述は過去の仕様として残す。

---

## 12. v3.2 追加仕様（2026-07-12）

### 12.1 Persona Studio 再設計

タブ構成: 4→3（テキスト入力廃止、機能は固定フォームに統合）

データフロー: currentDraft オブジェクト廃止 → hasDraft フラグ + DOM直読。保存/テスト時に毎回DOMから値を読み取り。

固定フォーム: 全24フィールド、全RP世界共通。ペルソナIDと名前のみ必須、他は任意。
基本情報: 身体的性別, 性自認・表現, 年齢, 誕生日, 種族, 血液型, 身長, 体重, BWH
外見: 髪, 目, 肌, 服装スタイル
人物: 性格, 一人称, 二人称, 口調, 口調サンプル, 好き嫌い, 癖・習慣
立場: 職業/所属, 年収/生活水準, 特殊能力/スキル
その他: 背景, 禁止事項

ファイルから生成: 固定フォーム内に生テキスト貼り付けエリア。convert-freetext APIで変換後 fillTemplateForm() で抽出反映。

タブ間同期: persona-id は t ↔ d 双方向。スタイルは1セットのみ。

読込時抽出: fillTemplateForm() で ## ■ 見出し・bullet形式両対応、全文横断検索。基本情報セクションを機械抽出。

### 12.2 バックエンド

TEMPLATE_PROMPT: 全24フィールド注入。SOUL.md内に機械抽出用セクション必須化。
create_via_template: fields オブジェクト + style_override から読取。
convert_freetext: max_tokens 16000。
list_personas: updated（SOUL.md mtime）返却、降順ソート。
_openai_sync: 空レスポンス時に finish_reason ログ。

### 12.3 ペルソナ一覧

表示形式: 更新日時 キャラ名（降順）
クリック読込 / ダブルクリック削除

### 12.4 セッション設定

?persona= 自動選択: loadPersonas() 完了後に実行。
このキャラで新規 → キャラ選択スキップ、文体選択へ。
指定ペルソナ不在時は先頭にフォールバック。

### 12.5 チャット削除

deleteMessage(): DOM操作廃止、loadHistory() でサーバー状態から再構築。

---

## 13. v3.3 CharacterData 中心設計（2026-07-12）

### 13.1 背景と目的

v3.2 までの Persona Studio は「フォームを中心」とした設計だった。

```
生テキスト → LLM → SOUL.md(自由形式) → 正規表現 → フォームフィールド
```

この方式には以下の構造的問題があった：

1. LLM出力が自由形式Markdownのため、後段の正規表現抽出が不安定
2. 2段階の情報減衰（LLM要約→正規表現抽出）
3. フォーム・SOUL.md・SKILL.mdの間でデータの二重管理が発生

v3.3 では **CharacterData** をシステム唯一の正（Source of Truth）とし、全コンポーネントがこれを読み書きする設計に移行する。

### 13.2 CharacterData（`backend/core/character_data.py`）

```python
@dataclass
class CharacterData:
    # 基本情報（8）
    persona_id, name, sex, gender, age, birthday, species, blood
    # 身体（3）
    height, weight, bwh
    # 外見（4）
    hair, eyes, skin, clothing
    # 人物（8）
    personality, principles, firstperson, secondperson, tone, speech, likes, habits
    # 立場（2）
    occupation, skills
    # その他（2）
    background, forbidden
    # 余剰データ
    extra_sections: list[dict]  # [{"title", "content"}, ...]
```

**v3.2からの変更点**:
- `principles`（行動原理・判断基準）追加。personality（性格）と分離
- `income`（年収/生活水準）削除。background に統合
- `extra_sections` 追加。フォームに収まらない情報を保持・捨てない

### 13.3 新フロー

```
入力テキスト（任意形式）
    │
    ▼
POST /api/persona-studio/extract-fields  【新API】
    │  LLMにSOUL.mdではなく構造化JSONでフィールド値を直接抽出させる
    │  「要約禁止」「情報削除禁止」をプロンプトに明示
    │  出力: { fields: {...}, extra_sections: [...] }
    │
    ▼
[ブラウザ] fillFormFromFields(fields)  → 全フィールドに直接セット
    │  正規表現廃止。JSONキー → フォームID の直接マッピング
    │
    ▼
[ユーザー] 抽出結果を確認・編集
    │
    ▼
[既存] generateFromTemplate() → create_via_template API → SOUL.md/SKILL.md生成
```

### 13.4 抽出プロンプト設計

`EXTRACT_FIELDS_PROMPT`（`plugin.py`）の要点：

- **役割**: 「情報抽出エンジン」。生成ではなく分類
- **最重要ルール**: 要約禁止、情報削除禁止、意味変更禁止
- **出力形式**: 全26フィールドのJSON + `extra_sections`
- **分類指針**: 意味的に最も近いフィールドに全文を入れる。該当なしは `extra_sections` へ
- **personality / principles の区別**: フィールド説明に定義と例を明示（Sonnet指摘対応）

### 13.5 API

| Method | Endpoint | 機能 |
|--------|----------|------|
| `POST` | `/api/persona-studio/extract-fields` | 自由テキスト → 構造化フィールド抽出（新） |
| `POST` | `/api/persona-studio/convert-freetext` | 自由テキスト → SOUL.md生成（旧、非推奨） |

### 13.6 フロントエンド

| 関数 | 変更 |
|------|------|
| `extractFields()` | 新規。extract-fields API → fillFormFromFields() |
| `fillFormFromFields(fields)` | 新規。JSONキー→フォームID 直接マッピング。正規表現廃止 |
| `fillTemplateForm(soul)` | 変更。principles 抽出追加（既存ペルソナ読込用、旧方式互換） |
| `convertRawText()` | 存続。旧ボタンからの呼出用、非推奨 |

### 13.7 フィールド一覧（最終、26+1）

```
基本情報: persona_id(SYS), name, sex, gender, age, birthday, species, blood
身体: height, weight, bwh
外見: hair, eyes, skin, clothing
人物: personality, principles, firstperson, secondperson, tone, speech, likes, habits
立場: occupation, skills
その他: background, forbidden
余剰: extra_sections
```

### 13.8 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `backend/core/character_data.py` | **新規**。CharacterData dataclass |
| `backend/plugins/persona_studio/plugin.py` | EXTRACT_FIELDS_PROMPT追加、extract_fields()追加、TEMPLATE_PROMPT改訂（principles追加/income削除/extra_sections統合指示追加）、_format_extra_sections()追加 |
| `backend/main.py` | POST /api/persona-studio/extract-fields 追加 |
| `frontend/js/studio.js` | fillFormFromFields()追加、extractFields()追加、addExtraSection/removeExtraSection/getExtraSections/setExtraSections追加、generateFromTemplate/saveDraft/loadDraft/resetAll改修、ALL_T_FIELDS更新 |
| `frontend/studio.html` | principles欄追加、income欄削除、自由設定fieldset追加、ボタン文言変更 |
| `frontend/js/i18n.js` | fieldPrinciples/btnExtractFields/statusNeedText/fieldsetExtra/hintExtra/btnAddSection追加、fieldIncome/btnConvertRaw削除 |

### 13.9 自由設定（extra_sections）UI

固定フォーム下部に「自由設定」セクションを追加。

- タイトル（任意）＋内容（textarea）のペアを `[+ 追加]` ボタンで自由に追加可能
- `extractFields()` のAPI応答に含まれる extra_sections は自動で反映
- 手入力でゼロから追加も可能
- 各項目に削除ボタン
- DOMが唯一の状態（状態変数保持なし）

### 13.10 SOUL.md生成への統合

TEMPLATE_PROMPT に補足情報の扱いを明示：

- 削除禁止、情報を捨てない
- 本文の適切な場所へ自然に統合
- 統合できない情報のみ末尾に「## 補足情報」として残す

---

## 14. v3.3 セッション管理・状態追跡（2026-07-12）

> **履歴注記**: §13と§14はいずれもv3.3開始時の記録です。§14には後続のv3.4 / v3.4.1 / v3.4.2変更が同一章へ累積されています。履歴保持のため章見出しは変更しません。

### 14.1 セッション自動復元

**問題**: `POST /api/chat` を含む全チャットAPIが `persona_manager.active`（グローバル状態）のみに依存し、フロントが persona_id/session_id を送信していなかった。サーバー再起動や別タブでのセッション切替により、意図しないキャラクターにメッセージが送られる事故が発生。

**対策**:

| エンドポイント | 変更 |
|--------------|------|
| `POST /api/chat` | `persona_id` + `session_id` 受取。不一致時は `_auto_resume_session()` で自動復元 |
| `GET /api/session/history` | Queryパラメータで persona_id/session_id 受取 |
| `POST /api/session/update-message` | 同上 |
| `POST /api/session/delete-message` | 同上 |
| `POST /api/session/truncate` | 同上 |

**フロント**: `localStorage["rp-session"]` に `{persona_id, session_id}` を永続化。ページ再読み込み時にサーバー側セッションが消失していても localStorage から自動復元。

### 14.2 `on_session_end` 発火修正

**問題**: `on_session_end` フックが `plugin_manager.py` に定義されているだけで一度も dispatch されていなかった。このため session_log（セッションMarkdownログ）と memory（セッション終了時RAG事実抽出）が全く機能していなかった。

**修正**: 以下のタイミングで `on_session_end` を dispatch:
- サーバー停止時（lifespan shutdown）
- `start_session` で別ペルソナに切り替え時
- `resume_session` で別ペルソナに切り替え時

### 14.3 開始時状況説明（Opening Scene）

**フロー**:
```
ペルソナ作成時:
  Studioフォーム → opening_scene 入力
    ├─ 記入あり → 不足情報をLLMが補足 → SOUL.md に ## 開始時の状況 として保存
    └─ 記入なし → LLMが設定から自動生成 → SOUL.md に保存

チャット開始時:
  新規セッション（?new=1） → /api/session/opening → SOUL.md から読取のみ
  既存セッション → 呼出なし
```

**CharacterData 追加フィールド**: `opening_scene`

### 14.4 状態追跡システム（State Tracking）v3.4 — バックエンド差分計算方式

**LLMは現在の全状態をシンプルに列挙するだけ。差分（新規/変更/削除/維持）はバックエンドが機械的に計算する。**

```
LLM出力形式（タグ・カテゴリ・__DEL__ すべて不要）:
---STATE---
- 対象の両手拘束: 手枷で背後に固定
- 対象の首輪: 装着中
- 葵依の居場所: 隣室、モニター越しに監視中
- 葵依と対象の約束: 10分後に手枷を外す
```

**LLMへの指示**: 項目名に誰の状態かを含める（例: `対象の両手拘束` `葵依の居場所`）。拘束状態・約束・時間経過要素・忘却すべきでない情報すべてを列挙。変化がなくても毎回全項目を出力。

**バックエンド処理**:
1. フラットパース → `state_dict`（`{key: value}`）
2. `_state.json` から前回状態を読込
3. `_diff_state(old, new)` で4状態判定（new/changed/unchanged/deleted）
4. `_save_session_state(new)` でフル上書き保存
5. 差分情報を SSE `type: "state"` でフロントへ送信

**フロント表示**: 色分け（新規=緑`#22c55e`、変更=黄`#eab308`、削除=赤取消線`#ef4444`、維持=グレー）。

**保存形式**: `sessions/{persona_id}/{session_id}_state.json`。フラットな `{key: value}` 辞書。

**設計意図**: 
- LLMにタグ付けや分類を任せない（ミスの排除）
- 差分判定をPythonの決定的な辞書比較で行う（信頼性）
- 項目の省略＝削除（LLMが `## Current State` で全項目を認識済みのため、意図しない削除は発生しにくい）

**障害耐性**:
| 障害 | 挙動 |
|------|------|
| LLMが `---STATE---` を出力しない | 更新スキップ、旧状態維持 |
| LLMが項目を1つ列挙し忘れ | 削除扱い（ただし次回プロンプトに全項目注入済みのため発生確率低） |
| `_state.json` 破損 | 旧状態なし→全項目「新規」扱いで復旧 |
| `---STATE---` がSSEチャンク境界で分割 | `pending` バッファで検出（v3.3修正） |

### 14.5 フィールド変更

| 変更 | 内容 |
|------|------|
| `principles` 追加 | 行動原理・判断基準。`personality`（性格）と分離 |
| `income` 削除 | `background` に統合 |
| `opening_scene` 追加 | セッション開始時の状況説明 |

### 14.6 自由設定（extra_sections）

固定フォーム下部に「自由設定」セクションを追加。タイトル＋内容のペアを自由に追加可能。
- `extractFields()` で取得した余剰情報は自動反映
- 手入力でゼロから追加も可能
- Generator（TEMPLATE_PROMPT）が統合指示に従ってSOUL.mdに反映
- DOMが唯一の状態（状態変数保持なし）

### 14.7 その他

| 項目 | 変更 |
|------|------|
| ログローテーション | 10MB×3 → 1MB×2 |
| `persona_manager` デバッグログ | `switch()` / `ensure_active()` に変動監視ログ追加 |
| `chat_sse` デバッグログ | `active` が空の場合の警告追加 |
| フィールド抽出API | `POST /api/persona-studio/extract-fields` — LLMがSOUL.mdではなく構造化JSONを返す |

### 14.8 v3.4 状態追跡再設計 + セキュリティ堅牢化（2026-07-12）

#### 14.8.1 状態追跡（State Tracking）バックエンド差分計算方式

§14.4 に詳細記載。LLMはタグなしフラットリストを出力。差分は `_diff_state()` で計算。表示は色分け。

#### 14.8.2 セキュリティ修正

| 項目 | 修正 |
|------|------|
| `_auto_resume_session` session_id バリデーション | `resume_session` と同じ正規表現 `\d{4}-\d{2}-\d{2}_\d{8}` を追加 |
| `_auto_resume_session` + `start_session` persona_id バリデーション | 防御的 `validate_persona_id()` を入口で追加 |
| `escapeHtml` XSS対策 | `sessions.js` `session-setup.js` `settings.js` のタイポ修正。全5ファイルを完全なHTMLエンティティ変換に統一（`&lt;` `&gt;` `&quot;` `&#39;` `&amp;`） |
| `_PERSONA_ID_RE` ASCII限定化 | `[\w\-]+` → `[a-zA-Z0-9_\-]+`（Python 3の `\w` は日本語を含むため） |

#### 14.8.3 堅牢性修正

| 項目 | 修正 |
|------|------|
| `history.py` `_save_full()` | 直接上書き → 一時ファイル書き込み + `os.replace()` アトミック置換。クラッシュ時のデータロスト防止 |
| `_auto_resume_session` on_session_end | 別ペルソナ自動切替時に session_log + memory 抽出が走るよう dispatch 追加 |
| `---STATE---` SSEチャンク跨ぎ | `pending` バッファで末尾12文字保留→次チャンクと結合検出 |
| `chat.js` ハードコードデフォルト | `activePersonaId = "aoi-dystopia"` → `""` |

#### 14.8.4 リファクタリング（v3.4.1）

| 項目 | 内容 |
|------|------|
| `core/config.py` `update_config_yaml()` | YAML読み書きパターンをヘルパー化。5つの設定APIの重複を排除 |
| `_dispatch_session_end_for_active()` | on_session_end dispatch を1箇所に集約 |
| `_activate_session()` | switch+reload+スタイルロック+rebuild+保存の共通コアを抽出 |
| `_auto_resume_session` + `resume_session` | 共通コア抽出により170行の重複を解消。バリデーションロジックの一元化 |

#### 14.8.5 設定ファイル堅牢化（v3.4.2 — 2026-07-12）

Geminiコードレビュー指摘対応。

**① PyYAML → ruamel.yaml**

`core/config.py` の YAML ライブラリを `PyYAML` から `ruamel.yaml` に置換。
`update_config_yaml()` による設定書き戻し時に、ユーザーが手動で記述した `#` コメントが消失する問題を解決。

```python
from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
```

**② 設定ファイルのアトミック書き込み**

`update_config_yaml()` の書き込み処理を、直接上書き（`open(path, "w")`）から一時ファイル経由の `os.replace()` に変更。
§14.8.3 の `history.py` と同パターン。ディスクフルや書き込み中クラッシュ時のファイル破損を防止。

```python
temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
with open(temp_path, "w", encoding="utf-8") as f:
    _yaml.dump(raw, f)
os.replace(temp_path, config_path)
```

#### 14.8.6 Git管理・ドキュメント整備（v3.4.2 — 2026-07-12）

| 項目 | 内容 |
|------|------|
| `.gitignore` | 機密情報（`.env`）、ユーザーデータ（`sessions/` `session-log/` `data/`）、ペルソナホワイトリスト（`_template` `kyouka-detective` のみGit管理） |
| `.gitattributes` | 改行コード統一（ソースコード LF、`.bat` CRLF、`.sh` LF） |
| `README.md` | プロジェクト概要・Why・Quick Start・依存関係・データ保存先・免責事項 |
| `requirements.txt` | 必須パッケージ: `fastapi uvicorn httpx pyyaml python-dotenv ruamel.yaml` |
| `config.default.yaml` | 全セクションに日本語コメント追記（用途・指定方法・値の範囲） |
| `.env.example` | APIキー発行元URL・Gmailアプリパスワード発行手順を明記 |
| リポジトリ名 | `charachat` → `minichat-pchan`（GitHub上でリネーム、リモートURL自動追従） |

## 15. v3.5 Geminiコードレビュー対応 + 堅牢化（2026-07-13）

### 15.1 Pydantic リクエスト検証

全22のPOSTエンドポイントを `data: dict` から Pydantic `BaseModel` に変換。型検証・必須項目チェック・Swagger自動生成が有効化された。

```python
class UpdateMessageRequest(BaseModel):
    index: int
    content: str
    persona_id: str = ""
    session_id: str = ""
```

`chat_sse` と `create_template` は複雑なdict構造のため据え置き。

### 15.2 config二重管理解消

`update_config_yaml()` でファイルに書き戻した後、メモリ上の `config` に同じ値を手動で再設定していた重複を排除。同じ `mutator` 関数を `config` にも適用する方式に統一。

```python
# 変更前
update_config_yaml(CONFIG_PATH, mutator)
config["active_provider"] = provider  # 二重記述
config["active_model"] = model

# 変更後
update_config_yaml(CONFIG_PATH, mutator)
mutator(config)  # 同一mutatorをメモリにも適用
```

### 15.3 JSONDecodeError ログ追加

`core/api.py` の3プロバイダのストリーミング関数で SSEパース例外をサイレントに `continue` していたのを修正。`logger.warning()` で記録。

### 15.4 persona_id バリデーション 3層防御

| 層 | 場所 | 内容 |
|----|------|------|
| HTML | `studio.html` | `pattern="[a-zA-Z0-9_-]+"` `maxlength="64"` |
| JS | `studio.js` | `validatePersonaId()` + `input` イベントでリアルタイム検出 |
| Backend | `persona_manager.py` | `_MAX_PERSONA_ID_LEN = 64` + `re.fullmatch` |

`resetAll()` / `loadDraft()` 後のバリデーション再評価も対応。`t-income` の死に参照2行を削除（v3.3でフィールド廃止済み）。

### 15.5 extract_fields バッチ分割

OpenCode Go無料枠の出力制限（`finish_reason=length`）対策として、27フィールドを `_EXTRACTION_BATCH_SIZE = 10` ごとのバッチに分割。`_build_extraction_prompt()` に `fields` パラメータを追加し、サブセット抽出を可能にした。

```python
_EXTRACTION_BATCH_SIZE = 10
batches = [all_fields[i:i + _EXTRACTION_BATCH_SIZE] for i in range(0, len(all_fields), _EXTRACTION_BATCH_SIZE)]
```

フィールド数が増減しても自動でバッチ数が調整される。

### 15.6 DeepSeek推論モデル `reasoning_content` 対応

`deepseek-v4-pro` は出力を `content` ではなく `reasoning_content` フィールドに格納する。`_openai_sync()` / `_openai_stream()` にフォールバックを追加。

```python
content = msg.get("content", "")
if (not content or not content.strip()) and msg.get("reasoning_content"):
    content = msg["reasoning_content"]
```

### 15.7 抽出API タイムアウト延長

- サーバー側: `_make_config(max_tokens=16000, timeout=300)` — 1バッチあたり最大300秒
- クライアント側: `AbortController(300000)` — 3バッチ合計で最大300秒
- 入力6000文字超過時にトリム

### 15.8 ロガー名統一

`api.py` と `persona_studio/plugin.py` 内のロガー名が `rp_standalone`（アンダースコア）だったのを `rp-standalone`（ハイフン）に統一。main.py がハンドラを設定しているロガーと一致せず、全ログがサイレントに破棄されていた問題を修正。

### 15.9 asyncio.Lock ✅ 適用済み

複数ブラウザタブからの同時リクエストによるデータ競合を防止するため、`_api_lock = asyncio.Lock()` を追加。ペルソナ切替、セッション開始・再開、履歴編集、チャット送信等の主要エンドポイントに適用済み。

### 15.10 UI 改善

| 項目 | 変更 |
|------|------|
| 設定画面プロバイダ表示 | 横並び「現在: provider / model」→ 縦2行「プロバイダ:」「モデル:」 |
| Studio ペルソナ一覧 | persona_id を11px薄字で追加、日付+ID左寄せ・名前右寄せ |
| raw-text エリア | 「ファイルから生成」→「テキストから生成（貼り付け）」、文字数カウンター追加 |
| escapeHtml | `studio.js` のシングルクォート未対応 + nullガードなしを修正、全6ファイル統一 |

### 15.11 プロバイダ設定

| 項目 | 内容 |
|------|------|
| OpenCode Zen 追加 | `config.default.yaml` に `opencode-zen` プロバイダ追加、base_url `https://opencode.ai/zen/v1` |
| プロバイダキー名整理 | `opencode` → `opencode-zen` にリネーム |
| README | `Supported Providers` セクション追加、検証状況テーブル（✅/⚠️）、プロバイダ数固定表記廃止、Google API→Gemini API |

### 15.12 削除・クリーンアップ

| 項目 | 内容 |
|------|------|
| `import yaml` | `main.py` L25の死にimportを削除（ruamel.yaml移行済み） |
| `STATE` 最大長 | `MAX_STATE_LENGTH = 4096` 追加 |
| `t-income` 参照 | `fillTemplateForm()` 内のv3.3廃止フィールド参照2行を削除 |

---

## 16. v3.6 軽微修正・内部改善（2026-07-13）

> **注**: v3.6 は軽微な内部改善・バグ修正が中心のため、独立した章としての詳細記録は省略。
> v3.5 → v3.7 の差分については §15（v3.5 追加仕様）および §17（v3.7 品質改善）を参照。

---

## 17. v3.7 品質改善 + 新機能（2026-07-13）

### 17.1 DeepSeek V4 Pro 調査・デバッグ強化

**生APIレスポンスダンプ機構**（`core/api.py`）:
- `API_DEBUG_DUMP=1` 環境変数で全APIレスポンスを `logs/api_debug/` にJSON保存
- content空等の異常系は環境変数に関わらず常に自動保存
- 保存内容: リクエスト要約 + レスポンスヘッダ + 生body + content/reasoning分析
- トップレベル `summary` フィールドでファイルを開かずに傾向スキャン可能

**実測データ**:
- OpenCode Go + DSv4P の応答: `{"message": {"content": "...", "reasoning_content": "..."}}`（仕様通り）
- 正常時: `reasoning_tokens` が `completion_tokens` の92〜97%を消費
- 長文抽出時: `finish_reason=length` + content空が高確率で発生（Reddit既知）
- `reasoning_effort` パラメータ: OpenCode Go非対応（400/503確認済み）
- 参考資料: `reference/DeepSeekIssue.txt`, `reference/DeepSeek-review-2026-07-13.txt`

### 17.2 環境分離

**専用venv**（`.venv/`）:
- RPアプリ専用のPython仮想環境を作成。Hermes Agentのvenvと分離
- `start_server.bat`: 専用venvのpythonを使用 + `PYTHONPATH=` クリア + `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` 設定
- `requirements.txt`: `sentence-transformers` `chromadb` を追記
- `.gitignore`: `backend/logs/api_debug/` `backend/data/` `/reference/` `.venv/` 追加

### 17.3 History セッション日付修正

**`core/history.py`**:
- `_session_date` フィールド追加。`set_session_id(sid, date)` で日付を受け取る
- `session_file` プロパティが日付を正しく使うよう修正
- 過去セッション再開時に今日の日付で空ファイルが作られるバグを修正

**影響範囲**:
- `_activate_session()`: `session_date` パラメータ追加
- `resume_session()`: ファイル名から日付を抽出して渡す
- `_auto_resume_session()`: session_id から日付を抽出
- `list_sessions()`: `.current-session` から `session_date` を読み取り

### 17.4 エラー時ログ保存

**`chat_sse` エラーハンドリング**:
- エラー時に `history.pop()` で履歴破棄 → エラーマーカー `[ERROR: code]` 付き保存に変更
- `history.save_turn()` + `on_response_complete` ディスパッチをエラー時も実行
- ユーザー入力が失われず、session_log にも記録される

### 17.5 ツール呼び出し抑制

**`rebuild_system_prompt()`**:
- システムプロンプトに「出力制約」セクション追加
- ツール呼び出し・コード実行ブロック（```` ``` ````）の出力を禁止
- DeepSeek V4 Pro が英数字入力で `terminal()` 構文を出力する問題に対応

### 17.6 生成中断機能

**バックエンド**:
- `_cancel_event = asyncio.Event()`（グローバル）
- `POST /api/chat/cancel`: チャット生成中断
- `POST /api/persona-studio/cancel`: 抽出・生成中断
- `chat_sse` のストリーミングループでキャンセルチェック → `type: "cancelled"` SSEイベント送信
- `persona_studio/plugin.py`: バッチループでキャンセルチェック → `CancelledError` 送出

**フロント**:
- チャット画面: 「停止」ボタン（`#stop-btn`）、生成中のみ表示
- Persona Studio: ローディングオーバーレイに「停止」ボタン
- `AbortController` で fetch 中断 + バックエンドキャンセルAPI呼出
- SSE `cancelled` イベント受信 → `[中断]` 表示

### 17.7 memory_scope（記憶スコープ）

**設計**:
- セッション開始時に「このセッションのみ」/「全セッション共通」を選択
- ChromaDBメタデータ: 常に `persona_id` + `session_id` を保存
- クエリ時: スコープに応じてフィルタ切替
  - `"session"`: `$and: [{persona_id}, {session_id}]`
  - `"persona"`: `{persona_id}` のみ
- セッション中に変更可能。データ移行不要

**変更ファイル**:
- `core/session_context.py`: `memory_scope: str = "session"`
- `main.py`: `StartSessionRequest.memory_scope`, `_get_current_memory_scope()`, `get_current_session()` 返却値追加
- `plugins/memory/plugin.py`: 保存時 `session_id` 追加、クエリ時スコープフィルタ
- `frontend/session-setup.html`: 記憶スコープ選択UI
- `frontend/js/i18n.js`: 日英6キー追加

### 17.8 UI改善

- **開幕状況説明**: `generate_opening()` に不在時フォールバック（ペルソナ名から自動生成）
- **状況パネル**: 新規セッションでも常時表示（空時は「変化なし」）
- **i18n**: チャット画面の編集/再生成/削除ボタンを `t()` 化、`[編集済]` も対応
- **キャラ選択カード**: コンパクト化（`minmax(160px,1fr)`, padding縮小）
- **連打防止**: `continueSession` に `_continueLock` ガード
- **言語状態保持**: `chat.js` の DOMContentLoaded に `i18nApply()` + `updateLangToggle()` 追加

### 17.9 バグ修正（要調査より）

| # | 内容 | 修正 |
|---|---|---|
| 調査1 | 再開時に0ターンセッションが新規作成 | `session_date` 保存で重複排除 |
| 調査2 | AI応答エラー時にセッションログ未保存 | エラー時も `save_turn` + `on_response_complete` |
| 調査4 | 同一キャラのログ混入 | memory_scope 実装 + `session_date` 修正 |
| #8 | 英数字入力でツール呼び出し漏れ | 出力制約追加 |
| #11 | 下書きペルソナ混入 | APIに `?status=saved` フィルタ |

### 17.10 未了・保留

| 項目 | 状態 |
|---|---|
| DeepSeek V4 Pro 抽出問題 | v3.8 で抽出タスク用フォールバックチェーンとして解決 |
| 応答言語の設計 | 設計検討中（SOUL.mdの「必ず日本語」問題） |
| 初回セッション削除不具合 | 調査1の修正で改善見込み、再現待ち |
| 抽出中リロードの挙動 | 設計未了 |


---

## 18. v3.8 抽出タスク用フォールバックチェーン（2026-07-14）

### 18.1 背景

OpenCode Go 経由の DeepSeek V4 Pro では、`reasoning_content` が completion_tokens の92〜97%を消費し、
抽出などの長文・構造化出力タスクで `content` が空になる問題が高確率で発生する。
OpenRouter 経由の同一モデルでは正常動作することから、OpenCode Go プロキシ層の問題と断定。

3つの外部AI（GPT / Gemini / Sonnet）に設計レビューを依頼し、
「抽出専用のプロバイダ・モデル設定分離」＋「優先順位付きフォールバックチェーン」方式を採用。

### 18.2 設計方針

- 抽出・生成タスクは `config.yaml` の `extraction.fallback_chain` に設定されたプロバイダ/モデルを**上から順に試行**し、最初に成功した段で確定
- content 非空（+ 抽出の場合は JSON パース可能）を成功条件とする
- 未設定の場合は `active_provider` / `active_model` を使用（後方互換）
- 全滅時は `ValueError`（どのモデルでもダメだったことを明示）
- どの段で成功したかログに記録（後日のチューニング用）

### 18.3 config.yaml 追加項目

```yaml
extraction:
  fallback_chain:
    - provider: opencode-go
      model: deepseek-v4-pro
    - provider: openrouter
      model: nvidia/nemotron-3-ultra-550b-a55b:free
    - provider: openrouter
      model: deepseek/deepseek-v4-pro
```

### 18.4 バックエンド変更

**`plugins/persona_studio/plugin.py`**:

- `_make_config()` — `provider`, `model` パラメータ追加（active_provider/active_model の上書き用）
- `_get_fallback_chain()` — config.yaml からチェーン取得。未設定時は active を単一エントリとして返す
- `_try_with_fallback()` — content 非空チェックでチェーン試行。成功時点で返却、全滅で ValueError
- `_try_extraction_chain()` — 同上 + JSON パース検証付き（`extract_fields` 用）
- `extract_fields()` — 旧リトライロジック（20秒待機 + 同モデル再試行）を `_try_extraction_chain()` に置換。バッチ間待機を 10秒 → 3秒に短縮
- `create_via_template()` — `chat_sync` + 空チェック → `_try_with_fallback()` に置換
- `convert_freetext()` — 同上

**`main.py`**:

- `ExtractionConfigRequest` モデル追加（`fallback_chain: list[dict]`）
- `POST /api/config/extraction` — バリデーション（provider存在確認）→ `update_config_yaml()` で保存 → メモリ反映

### 18.5 フロントエンド変更

**`frontend/settings.html`**:

- 設定画面に「抽出タスク用モデル」タブ追加
- 優先枠のリスト（Provider/Model の select）、＋追加ボタン、適用ボタン

**`frontend/js/settings.js`**:

- `loadExtractionChain()` — 設定読み込み → チェーンリスト描画
- `renderChainEntry()` — 動的HTML生成（優先nバッジ、Provider/Model選択、削除ボタン）
- `onChainProviderChange()` — Provider変更時にModel選択肢を動的フィルタ
- `addChainEntry()` / `removeChainEntry()` — 枠の追加/削除（最大5、最小1）
- `collectChainData()` — UIの全エントリを収集して `POST /api/config/extraction` に送信
- 全関数に try-catch + console.error のエラーハンドリングあり
- ボタンイベントは `addEventListener` 方式（`onclick` 属性不使用）
- 日英i18n対応（`chainPriority`, `chainSelectProvider`, `chainRemove` 等7キー追加）

**`frontend/css/style.css`**:

- `.chain-entry` / `.chain-entry-header` / `.chain-priority` / `.chain-remove-btn` / `.chain-entry-body` / `.chain-field` — チェーンエントリのカード型レイアウト

### 18.6 未了・保留

| 項目 | 状態 |
|---|---|
| 応答言語の設計 | v3.9 で global_system_prompt として解決 |
| 初回セッション削除不具合 | 再現待ち。v3.9 で削除ロック追加（防御的対策） |
| 抽出中リロードの挙動 | v3.9 で切断検知として解決 |


---

## 19. v3.9 ユーザー設定システムプロンプト + 切断検知 + UI改善（2026-07-14）

### 19.1 ユーザー設定システムプロンプト（global_system_prompt）

**背景**:
全モデル・全ペルソナ共通の出力指示（「必ず日本語で」「コードブロック禁止」等）を
一箇所で管理したい。これまで SOUL.md に「出力は必ず日本語」を個別に書いていたが、
グローバル設定に昇格させる。

**設計**:
- `config.yaml` の `global_system_prompt` フィールドに文字列で保存
- 空文字列の場合は何も注入しない（デフォルト）
- 注入位置: SOUL.md → SKILL.md → style → state → **global_system_prompt** → 出力制約 → 状態追跡
  - SOUL.md より後ろに置くことで、キャラクター定義を上書きせず、出力形式・文体の最終指示として機能
  - SillyTavern の Author's Note 配置と同等の戦略

**設定**:
```yaml
global_system_prompt: ""
```

**バックエンド変更**:
- `main.py`:
  - `SystemPromptRequest` モデル追加
  - `rebuild_system_prompt()`: グローバルプロンプトを state の後、出力制約の前に注入
  - `POST /api/config/system-prompt`: 保存エンドポイント
- `config.default.yaml` / `config.yaml`: `global_system_prompt` フィールド追加

**フロントエンド変更**:
- `settings.html`: 詳細タブにシステムプロンプトセクション追加（textarea + 文字数カウンター）
- `settings.js`: `loadSystemPrompt()` / `applySystemPrompt()` / `onSystemPromptInput()` 追加
- 推奨上限 1,500 文字（超過時は警告表示、保存・送信は制限なし）
- 日英 i18n 対応

**SOUL.md 修正**:
- `aoi-dystopia/SOUL.md`: `- 出力は必ず日本語` を削除
- `kyouka-detective/SOUL.md`: 同上

### 19.2 抽出中リロードの切断検知

**背景**:
Persona Studio の抽出処理中にブラウザをリロードすると、バックエンドが切断を検知せず
全バッチを完走し、APIトークンが無駄に消費されていた。

**設計**:
- サーバー側で TCP 切断をポーリング検知（`request.is_disconnected()`）
- 切断検知 → `_cancel_event.set()` → プラグインの既存 cancel チェックで `CancelledError` 送出
- フロント側でも `beforeunload` 時に `navigator.sendBeacon()` で cancel API を fire-and-forget

**バックエンド変更**:
- `main.py`:
  - `_run_with_disconnect_guard()` ヘルパー新設（cancel_event クリア + 切断監視バックグラウンドタスク）
  - 6エンドポイントを切断検知でラップ: `estimate-style`, `create-template`, `extract-fields`, `convert-freetext`, `refine`, `test-chat`
- `plugins/persona_studio/plugin.py`:
  - `_try_with_fallback()` ループ内に cancel チェック追加
  - `_try_extraction_chain()` ループ内に cancel チェック追加
  - 計3ヶ所で `CancelledError` 送出可能に（既存 batch ループ + 上記2ヶ所）

**フロントエンド変更**:
- `frontend/js/studio.js`: `beforeunload` に `sendBeacon("/api/persona-studio/cancel")` 追加

### 19.3 設定画面の項目説明充実

| 追加項目 | 内容 |
|----------|------|
| プロバイダ選択 | APIリクエストの送信先です。プロバイダを切り替えると利用可能なモデル一覧が変わります。 |
| アクティブモデル | チャット・RPで実際に使われるモデルです。変更後は「適用して再接続」で反映されます。 |
| Watchdog 有効 | 有効にすると、一定時間操作がない場合にメール通知を行います（Gmail SMTP設定が必要）。 |
| 出力 Max Tokens | ラベル名を「Max Tokens」→「出力 Max Tokens」に変更し、セッションのコンテキストトークンと区別。説明文追加 |

### 19.4 最上部に戻るボタン

- `frontend/css/style.css`: `#back-to-top` スタイル追加（固定右下、スクロール時のみ表示）
- `settings.html`, `studio.html`, `sessions.html`: ボタン HTML + スクロール検知スクリプト追加

### 19.5 タイムアウトメッセージ改善

- `frontend/js/i18n.js`:
  - `err_api_timeout` ja: 「APIリクエストがタイムアウトしました」→「応答が返ってきませんでした。モデルが混雑しているか、リクエストが重すぎる可能性があります。しばらく待ってから再試行してください。」
  - `err_api_timeout` en: 同様に技術用語を排除し、ユーザーフレンドリーな文言に

### 19.6 セッション削除の防御的対策

- `frontend/js/sessions.js`: `deleteSession()` に `_deleting` ロック追加（二重送信防止）

### 19.7 変更ファイル一覧

| 分類 | ファイル | 変更 |
|------|----------|------|
| config | `config.default.yaml` | `global_system_prompt` 追加 |
| config | `config.yaml` | 同上 |
| コア | `main.py` | `SystemPromptRequest`, `_run_with_disconnect_guard()`, `rebuild_system_prompt()` 変更, 6エンドポイント切断検知ラップ, `POST /api/config/system-prompt` |
| プラグイン | `plugins/persona_studio/plugin.py` | `_try_with_fallback`, `_try_extraction_chain` に cancel チェック追加 |
| ペルソナ | `aoi-dystopia/SOUL.md` | 「出力は必ず日本語」削除 |
| ペルソナ | `kyouka-detective/SOUL.md` | 同上 |
| フロント | `settings.html` | システムプロンプトセクション, 各項目 hint, back-to-top |
| フロント | `studio.html` | back-to-top |
| フロント | `sessions.html` | back-to-top |
| フロント | `js/settings.js` | `loadSystemPrompt`, `applySystemPrompt`, `onSystemPromptInput`, i18nキー追加 |
| フロント | `js/studio.js` | `beforeunload` に `sendBeacon` 追加 |
| フロント | `js/sessions.js` | `deleteSession` に `_deleting` ロック追加 |
| フロント | `js/i18n.js` | `err_api_timeout` 文言改善 |
| フロント | `css/style.css` | `#back-to-top` スタイル追加 |

### 19.8 未了・保留

| 項目 | 状態 |
|------|------|
| watchdog 汎用化（メール以外の通知手段） | 設計未了、低優先 |
| ボタン配置の整理・視認性改善 | デザイン検討必要、低優先 |
| チャット画面へのログ取得機能追加 | 低優先 |

---

## 20. v3.10 OpenAI互換APIストリーム処理改善（2026-07-15）

### 20.1 空の `choices` チャンク対応

**背景**:
OpenAI互換APIのストリーミング応答では、本文のチャンクとは別に、使用量情報のみを持つ最終チャンクが返る場合がある。
このチャンクは `choices: []` となるため、従来の `choices[0]` 参照で `IndexError` が発生し、
サーバーログに `API chunk parse error (openai): list index out of range` が記録されていた。
本文の生成と履歴保存は継続しており、チャット失敗ではないが、正常な補助チャンクを警告として扱っていた。

**処理仕様**（`core/api.py` / `_openai_stream()`）:
- JSON解析後に `choices` を取得する
- `choices == []` の場合は、使用量情報などの本文を含まない正常チャンクとして読み飛ばす
- `choices` を含む通常チャンクは、従来どおり `choices[0].delta.content` をストリーミング出力する
- JSON不正、`choices` キー欠損など、空配列以外の不正形式は従来どおり警告を記録して処理を継続する

**影響範囲**:
- 対象はチャット画面の OpenAI互換APIストリーミング応答のみ
- Anthropic形式、Google形式、非ストリーミングAPIの処理には影響しない
- 応答本文、推論内容のフォールバック、履歴保存の挙動は変更しない

**確認内容**:
- 通常の本文チャンクに続いて `choices: []` の使用量チャンクが届くケースをモック通信で確認
- 本文が正常に出力され、`API chunk parse error (openai)` 警告が発生しないことを確認
- 既存回帰テスト15件がすべて成功

---

## 21. v3.10 設定API検証 + 属性エスケープ（2026-07-16）

> **履歴注記**: §20と§21は同一v3.10系列の後続変更記録です。独立release境界を裏づける資料がないため、新しいversion番号を推定付与しません。

### 21.1 設定API値バリデーション

**背景**: 設定変更API（`/api/config/*`）はキーの許可リストのみで値の型・範囲チェックがなかった。フロント以外からのAPI直叩きで不正な値が `config.yaml` に書き込まれる可能性があった。

**実装**（`core/config.py`）:

| 検証関数 | 対象 | 制約 |
|----------|------|------|
| `validate_api_settings()` | `max_tokens` | 整数 100〜100,000 |
| | `temperature` | 数値 0.0〜2.0 |
| | `timeout` | 整数 10〜600 |
| `validate_session_settings()` | `max_tokens` | 整数 4,000〜200,000 |
| | `save_interval` | 整数 1〜100 |
| `validate_style_settings()` | `viewpoint` | `ai_character` / `user_character` |
| | `narration` | boolean |
| | `person` | `first` / `third` |
| `validate_watchdog_settings()` | `enabled` | boolean |
| | `check_interval` | 整数 10〜3,600 |
| | `levels` | 最大3件、`after` 10〜86,400 |

- 未知キーは `_validate_keys()` で一律拒否
- bool を int/number として誤受理しない（`type(x) is not bool` で厳密判定）
- `_bounded_int()` / `_bounded_number()` で範囲チェック

**影響ファイル**: `backend/core/config.py`, `backend/main.py`

### 21.2 セッション一覧の属性エスケープ

**背景**: `session-setup.js` のペルソナカードで `data-id="${p.id}"` が未エスケープだった。通常のペルソナIDはバリデーションされているが、手動で不正なディレクトリを作った場合に属性脱出が理論上可能だった。

**修正**（`frontend/js/session-setup.js`）:
```js
// 修正前: data-id="${p.id}"
// 修正後: data-id="${escapeHtml(p.id)}"
```

### 21.3 確認結果

- Python 構文チェック成功
- JavaScript 構文チェック成功
- 回帰テスト 15件成功
- `git diff --check` 問題なし

---

## 22. v3.11 機密情報UI 全面実装（2026-07-16）

### 22.1 概要

v3.10 までバックエンドのみ実装だった機密情報機能に、チャットUI・Persona Studio連携・APIを全面実装。

### 22.2 チャットUI

- **🔒 機密値挿入ボタン**: 入力欄に追加。クリックでラベル＋値入力ダイアログ → `{{secret:N}}` をカーソル位置に挿入
- **マスク表示**: `addMessage()` を `innerHTML` から安全なDOM構築方式にリファクタリング。`{{secret:N}}` を `●●●●●` + 👁ボタン要素として描画
- **一時表示**: 👁クリックで `POST /api/secrets/reveal` を呼出→実値表示。再クリックでマスク復帰。ページ再読込で常に再マスク
- **ストリーミング対応**: 未完成プレースホルダー（`{{sec` 等）を末尾バッファに保留、完成後にマスク描画
- **編集フロー**: テキストエリアにプレースホルダー文字列を表示。削除検出時は確認ダイアログ
- **i18n**: 日英17キー追加（チャット用11＋Studio用6）

### 22.3 バックエンドAPI（全4エンドポイント）

| エンドポイント | 機能 |
|---------------|------|
| `GET /api/secrets/status` | プラグイン有効状態（フロントの🔒ボタン表示判断） |
| `POST /api/secrets/register` | 機密値登録・プレースホルダー発行 |
| `POST /api/secrets/normalize` | テキスト内の `{{s:}}` ＋実値をプレースホルダー化 |
| `POST /api/secrets/reveal` | プレースホルダー→実値復号 |

`register`/`normalize`/`reveal` に同一オリジン確認。全4APIに `Cache-Control: no-store`。reveal はPOST限定＋レート制限（30回/60秒）。

### 22.4 安全対策

- `secrets_store.json` を一時ファイル置換（`temp → os.replace`）でアトミック保存
- サーバーログから機密ラベル・実値を排除（id のみ記録）
- Persona Studio: 全外部LLM呼出（5ヶ所）を `protect_text()` でフィルタリング
- `{{s: label: value}}` 互換構文は継続サポート

### 22.5 変更ファイル

| ファイル | 変更 |
|---------|------|
| `backend/main.py` | 4エンドポイント追加、`_same_origin()`、`_no_store_headers()` |
| `backend/plugins/secrets/plugin.py` | アトミック保存、`normalize_text()`、`protect_text()`、`get_entry()` |
| `backend/plugins/persona_studio/plugin.py` | `set_secret_filter()`、`_sanitize_messages()` |
| `frontend/index.html` | 🔒ボタン、登録ダイアログ |
| `frontend/js/chat.js` | `addMessage()` DOM構築化、`renderMessageText()`、`pendingSecretStart()`、編集フロー |
| `frontend/js/i18n.js` | 日英17キー追加（チャット用11＋Studio用6） |
| `frontend/studio.html` / `studio.js` | Persona Studio 機密対応 |
| `tests/test_regressions.py` | 15件（+6件） |

### 22.6 確認結果

- 回帰テスト 15件成功
- Python / JavaScript 構文チェック成功

### 22.7 短い機密値の自動置換除外（2026-07-16）

- `protect_text()` の登録済み実値による自動置換を3文字以上に限定
- 1〜2文字の値は登録を許可しつつ、通常文章への誤置換を防止
- 🔒ボタンおよび `{{s: ...}}` 互換構文による明示的なプレースホルダー化は短い値でも継続サポート
- 1文字・2文字・3文字の境界値と明示構文の回帰テストを追加

**変更ファイル**: `backend/plugins/secrets/plugin.py`, `tests/test_regressions.py`

**確認結果**: 回帰テスト16件成功、`git diff --check` 問題なし
### 22.8 DOM XSS優先経路の安全化（2026-07-16）

DOM挿入監査で確認したF1〜F3を修正。

- Studioのファイル検証エラーを `textContent` 表示へ変更
- style/preset/LLM推定結果をDOM APIで構築し、外部値をHTMLとして解釈しない方式へ変更
- セッション・ペルソナIDのイベントコード直接連結を廃止し、`dataset` と `addEventListener()` へ変更
- 保存済みペルソナ、セッション一覧、設定画面の表示構造をDOM構築化
- 表示用のインラインstyleをCSS classへ移行
- XSS優先経路の回帰テストを追加

**変更ファイル**: `frontend/js/chat.js`, `session-setup.js`, `sessions.js`, `settings.js`, `studio.js`, `frontend/css/style.css`, `tests/test_regressions.py`

**確認結果**: 回帰テスト17件成功、JavaScript構文チェック5ファイル成功、`git diff --check` 問題なし
### 22.9 `innerHTML`・インラインイベントの全廃（2026-07-16）

- フロントエンドJavaScriptに残っていた `innerHTML` をDOM API（`replaceChildren()`、`textContent`、DOMプロパティ）へ移行
- HTMLの `onclick` / `onchange` / `oninput` を廃止し、外部JavaScriptの `addEventListener()` へ統一
- セッション・設定・Studioの「トップへ戻る」インラインscriptを共通処理へ統合
- 設定画面のフォールバックチェーン、Studio自由設定、チャット状態表示等をDOM構築化
- 残存を防ぐ回帰テストを追加し、旧HTML文字列方式を前提としたテストを更新

**変更ファイル**: `frontend/*.html`, `frontend/js/*.js`, `frontend/css/style.css`, `tests/test_regressions.py`

**確認結果**: 回帰テスト18件成功、JavaScript構文チェック6ファイル成功、対象パターン残存0件、`git diff --check` 問題なし

**残タスク**: CSPのreport-only検証・正式適用、既存インラインstyleの整理
### 22.10 CSP Report-Only導入（2026-07-16）

- 全HTTP応答へ `Content-Security-Policy-Report-Only` を付与
- `script-src 'self'`、`style-src 'self'`、`connect-src 'self'` 等の自己オリジン制限を設定
- `object-src 'none'`、`base-uri 'none'`、`frame-ancestors 'none'` を設定
- `POST /api/csp-report` を追加し、違反内容を重複抑制してログへ記録
- レポート本文を16KBに制限し、URIのquery/fragmentをログへ残さない方式にした
- Report-Onlyのため現段階では画面動作を遮断せず、既存インラインstyleを検出可能

**変更ファイル**: `backend/main.py`, `tests/test_regressions.py`

**確認結果**: 回帰テスト19件成功、Python構文チェック成功、実ASGI応答でヘッダー付与・204受信・413サイズ制限・query除去を確認、`git diff --check` 問題なし

**実測結果**: ヘッドレスChromeで `/sessions`、`/setup`、`/chat`、`/settings`、`/studio` を巡回。`style-src-attr` 87件（Studio 73、setup 6、settings 4、chat 3、sessions 1）を確認し、HTMLの `style=` 87件と一致。script・connect等の違反は0件。

**残タスク**: インラインstyle 87件のCSS class移行、CSP正式適用
### 22.11 インラインstyle全廃・CSP正式適用（2026-07-16）

- HTML 5画面のインライン `style=` 87件（33種類）を共通・用途別CSS classへ移行
- Studio 73件、setup 6件、settings 4件、chat 3件、sessions 1件をすべて除去
- インラインstyle再混入防止の回帰テストを追加
- ヘッドレスChromeで全5画面を再巡回し、CSP違反0件を確認
- `Content-Security-Policy-Report-Only` から正式な `Content-Security-Policy` へ切替
- 違反収集APIは正式適用後の監視用として継続

**変更ファイル**: `frontend/*.html`, `frontend/css/style.css`, `backend/main.py`, `tests/test_regressions.py`

**確認結果**: 回帰テスト19件成功、JavaScript構文チェック6ファイル成功、HTMLの `style=` 0件、CSP再実測0件、`git diff --check` 問題なし

### 22.12 機密ファイルのPOSIX権限制限（2026-07-16）

- Linux/macOSで `secrets_store.json` と書込用一時ファイルを所有者のみ読み書き可能な `0600` に制限
- 一時ファイルは `os.open()` で作成時点から `0600` とし、平文が緩い権限で存在する時間窓を回避
- 既存ストアは読込前に `0600` へ補正し、権限設定失敗時は機密保持を優先して初期化を失敗させる
- Windowsでは既存のユーザーACLに委ね、POSIX権限操作をスキップ
- 新規保存、既存移行、エラー伝播、Windows分岐の回帰テストを追加

**変更ファイル**: `backend/plugins/secrets/plugin.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/backlog.md`

### 22.13 HTTPクライアント共有・接続プール再利用（2026-07-16）

- `core/api.py` のOpenAI互換・Anthropic・Google各同期／ストリーム計6経路で共有 `httpx.AsyncClient` を利用
- FastAPI lifespanでクライアントを生成・終了し、終了時はプラグインのAPI利用が完了してから `aclose()` を実行
- 接続上限20、KeepAlive上限10を設定し、TCP接続プールを再利用
- プロバイダごとの既存タイムアウトはリクエスト単位の `timeout` 指定として維持
- 初期化の冪等性、終了処理、クライアント再利用、タイムアウト、6経路移行を回帰テストに追加

**変更ファイル**: `backend/core/api.py`, `backend/main.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/backlog.md`
**確認結果**: 回帰テスト26件成功、Python構文チェック成功、共有クライアント利用6経路・タイムアウト指定6経路を確認、`git diff --check` 問題なし

### 22.14 memory同一事実の重複保存抑制（2026-07-16）

- 同一ペルソナ・同一セッション内で、NFKC・先頭箇条書き／番号・空白を正規化した完全一致事実を保存対象から除外
- 同一抽出結果内の重複と、旧タイムスタンプ形式IDで保存済みの既存文書を内容比較で検出
- ペルソナID・セッションID・正規化済み事実のSHA-256から決定的IDを生成
- ChromaDB保存を `add()` から `upsert()` へ変更し、同一IDの再保存を安全に処理
- 既存文書取得、埋め込み生成、保存を `asyncio.to_thread()` へ移し、イベントループのブロックを回避
- 全件重複時は埋め込み生成とDB書込をスキップ
- 意味的類似と別セッション間の統合は、セッションスコープ検索との互換性を優先して対象外

**変更ファイル**: `backend/plugins/memory/plugin.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`
**確認結果**: 回帰テスト30件成功、Python構文チェック成功、既存照合・決定的ID・`upsert()`・全件重複スキップを確認、`git diff --check` 問題なし

### 22.15 動的プラグインUI基盤 version 1（2026-07-16）

- `get_ui_slot()` の構造化UI定義をPluginManagerで収集・検証する基盤を実装
- 初期対応をbutton、`chat.input_actions`、`chat.toolbar` のみに限定
- HTML・JavaScript・CSS・未知フィールドを受け付けないallowlist検証を導入
- `GET /api/plugins/ui` と定義済みアクション専用の `POST /api/plugins/{plugin_name}/actions/{action}` を追加
- POSTは同一オリジン、16KB以下のJSON objectに制限し、プラグイン応答も固定形式・サイズで検証
- `PluginBase.handle_ui_action()` は既存プラグイン互換のデフォルト実装付きで追加
- `plugin-ui.js` をチャット本体から独立させ、DOM APIのみで描画。初期化・個別操作の失敗を隔離
- 二重初期化競合を世代番号で防ぎ、結果を共通フィードバック領域とCustomEventへ通知
- 既存6プラグインにはUI定義を追加せず、既存🔒ボタンも現状維持

**変更ファイル**: `backend/plugins/base.py`, `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/index.html`, `frontend/js/plugin-ui.js`, `frontend/js/i18n.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`
**確認結果**: 回帰テスト37件成功、Python・JavaScript構文チェック成功、実ASGI応答でUI定義取得・未定義アクション・異オリジン・サイズ超過を確認、CSP禁止パターンなし、`git diff --check` 問題なし
### 22.16 動的プラグインUI拡張 Phase 1（2026-07-16）

- UIスキーマをversion 2へ更新し、既存buttonに表示専用のseparator/statusを追加
- separatorはtype/idのみ、statusはtype/id/text/levelのみを許可する型別allowlist検証を導入
- statusのtextを1〜200文字、levelをinfo/success/warning/errorの4値に制限
- 表示専用コンポーネントをアクション公開対象から除外し、buttonとの混在を安全に処理
- フロントはDOM APIとtextContentで描画し、levelを固定CSSクラスへマッピング
- statusの動的更新、Studio/Settingsスロット、入力フォームは後続タスクとして維持
- 正常系、型別フィールド混入、不正level、text境界、表示要素のアクション非公開を回帰テストへ追加

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`
**確認結果**: 回帰テスト39件成功、Python・JavaScript構文チェック成功、CSP禁止パターンなし、`git diff --check` 問題なし
### 22.17 動的プラグインUI拡張 Phase 2（2026-07-16）

- UIスキーマをversion 3へ更新し、`studio.actions` と `settings.plugins` を追加
- 既存のbutton/separator/statusと型別allowlistを変更せず、4スロットで共通利用
- Studioヘッダーに操作スロット、Settingsのタブ外上部に常時表示スロットを追加
- Studio/Settingsへ共通フィードバック領域を追加し、`i18n.js` と既存画面スクリプトの後に `plugin-ui.js` を読込
- セッション未開始時は空のpersona_idを許容し、セッション必須条件をプラグイン側の責務として設計書へ明記
- 入力フォーム、status動的更新、複数スロット定義は後続タスクとして維持
- 4スロット受理、未知スロット拒否、3画面配置、読込順、空セッションコンテキストの回帰テストを追加

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/studio.html`, `frontend/settings.html`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`
**確認結果**: 回帰テスト40件成功、Python・JavaScript構文チェック成功、実ASGI応答でversion 3と3画面CSPヘッダーを確認、ヘッドレスChromeのChat/Studio/Settings巡回でCSP違反0件、`git diff --check` 問題なし
### 22.18 Python依存関係の整合性修復と通常起動回復（2026-07-16）

- memory関連依存を `sentence-transformers==5.6.0`、`transformers==5.12.1`、`huggingface-hub>=1.5.0,<2.0`、`chromadb==1.5.9` として明示
- `transformers==5.12.1` と `huggingface-hub==1.2.3` の不整合を解消し、既存環境を `huggingface-hub==1.23.0` へ更新
- READMEの導入手順を `python -m pip install -r requirements.txt` へ統一
- Windows CP932コンソールで診断ログが失われないよう、logger固定メッセージのem dashをASCII hyphenへ変更
- requirements宣言、実行環境バージョン、4パッケージimport、CP932エンコードの回帰テストを追加
- クリーンvenvでrequirementsから新規インストールし、`pip check`、import、実埋め込みモデル初期化（384次元）を確認
- lifespan有効の通常起動、memory/HTTP client初期化、プラグイン逆順shutdown、embedding解放、HTTP client終了、ポート解放を確認

**変更ファイル**: `requirements.txt`, `README.md`, `backend/main.py`, `backend/plugins/mail/plugin.py`, `backend/plugins/watchdog/plugin.py`, `backend/plugins/persona_studio/plugin.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`
**確認結果**: クリーンvenvと既存環境の `pip check` 成功、4パッケージimport成功、埋め込み初期化成功、回帰テスト42件成功、通常起動で主要3画面とプラグインUI APIが200、UnicodeEncodeError 0件、正常shutdown完了、8765番ポート解放、`git diff --check` 問題なし

### 22.19 動的プラグインUI拡張 Phase 3（2026-07-16）

- UIスキーマをversion 4へ更新し、buttonアクション応答の `data.ui_updates` から同一プラグインのstatusを動的更新可能にした
- 更新指定を最大10件、`component_id` / `text` / `level` の3フィールドだけに限定し、公開済みstatus ID、重複、文字数、levelをバックエンドでallowlist検証
- 1件でも不正な場合は更新全体を拒否し、button/separator、存在しないID、他プラグインのstatusへの更新を禁止
- フロントでも独立して同じ形式を検証し、`textContent` と固定levelクラスだけをall-or-nothingで更新
- 現在の画面に対象statusが存在しない場合は安全に無視し、既存アクションを失敗させない
- 更新方式はアクションレスポンス連動だけに限定し、ポーリング、SSE/WebSocket、状態永続化は対象外
- 入力フォームと複数スロット定義は後続タスクとして維持し、プラグイン開発ガイド整備をbacklogへ追加

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/js/plugin-ui.js`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト46件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.20 動的プラグインUI拡張 Phase 4（2026-07-16）

- UIスキーマをversion 5へ更新し、`get_ui_slot()` が従来の単一dictに加えて最大4件のlistを返せるようにした
- 1定義最大10件・合計最大40コンポーネント、slot重複禁止、component IDの全スロット一意性をplugin単位で検証
- 1件でも不正な定義があればplugin全体を拒否し、他pluginはpriority順で継続するall-or-nothing方式を採用
- 同一pluginの全定義から有効button actionとstatus IDを集約し、2番目以降のslotでもaction実行・status更新を可能にした
- 複数slotで同じaction名を共有可能とし、1つ以上の有効buttonがあれば公開、すべてdisabledなら拒否する仕様にした
- フロントは受信定義をplugin名のMapでグループ化し、非連続な定義もplugin単位で事前検証してから描画
- 不正pluginの部分描画を防ぎつつ他pluginを継続し、既存のDOM API・textContent・固定CSSクラス制約を維持
- 入力フォームは後続タスクとして維持

**変更ファイル**: `backend/plugins/base.py`, `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/js/plugin-ui.js`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト50件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.21 動的プラグインUI拡張 Phase 5（2026-07-16）

- UIスキーマをversion 6へ更新し、1〜10個の文字列fieldを持つformコンポーネントを追加
- form/fieldのID、action、label、required、max_length、placeholder、初期valueを型別allowlistで検証
- form actionのplugin内重複とbutton actionとの衝突を禁止し、不正定義はplugin単位で全拒否
- form送信payloadを `{form_id, values}` に固定し、全fieldの存在、未知field、文字列型、required、最大長をhandler呼出前に検証
- `form_id` が存在する場合だけform検証へ分岐し、既存button actionのpayload互換性を維持
- disabled formはバックエンドでaction非公開、フロントで全controlを操作不能にした
- フロントのaction通信をbutton/form共通処理へ統合し、DOM APIだけでlabel・text input・submit buttonを構築
- 送信中のcontrol無効化と復帰、既存feedback・CustomEvent・status動的更新をformでも共通利用
- passwordやsecrets属性を追加せず、機密値は環境変数・`.env`・secrets専用UIを使う境界を設計書へ明記
- textarea、select、checkbox、number、secrets連携は後続タスクとして維持

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト56件成功、Plugin UIテスト24件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.22 プラグイン開発ガイド・雛形整備（2026-07-16）

- 現行実装に基づくプラグイン開発ガイドを追加し、ライフサイクル、hook引数、priority/critical、UI version 6、action応答、テスト手順を集約
- button / separator / status / form、複数スロット、`ui_updates`、固定form payload `{form_id, values}` の制約を記載
- 外部入力、HTML、認証情報、ネットワークI/Oを扱う際のtrust boundaryと失敗隔離を明記
- `backend/plugins/_template/` にコピー用の最小プラグイン、README、package初期化ファイルを追加
- 雛形は `plugins.enabled` に追加せず、現行runtimeへ影響しない状態を維持
- ガイド、雛形、実際のPluginManager検証、設定非登録を照合する回帰テストを追加

**変更ファイル**: `backend/plugins/_template/__init__.py`, `backend/plugins/_template/plugin.py`, `backend/plugins/_template/README.md`, `document/plugin_development.md`, `document/README.md`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト58件成功、ガイド・雛形テスト2件成功、雛形Python構文チェック成功、`git diff --check` 問題なし

### 22.23 動的プラグインUI拡張 Phase 6（2026-07-16）

- UIスキーマをversion 7へ更新し、文字列form fieldへ `textarea` と `select` を追加
- fieldの `type` 省略時は `text` として正規化し、version 6プラグインとの互換性を維持
- text/textarea/selectを型別allowlistで検証し、型固有でない属性や未知field型を拒否
- select optionsを1〜50件、`{value, label}` の2属性だけに限定し、value重複、長さ、初期値を検証
- select送信値を公開済みoption valueとバックエンドで照合し、任意値をhandler呼出前に拒否
- フロントはDOM APIだけでtextarea/select/optionを構築し、option labelは `textContent` で設定
- 既存formの送信中無効化・復帰、feedback、CustomEvent、status更新処理を3field型で共通利用
- コピー用雛形へselect例を追加し、開発ガイド・設計書・backlogをversion 7へ同期
- checkbox / number、secrets連携、複数選択select、field単位disabledは後続タスクとして維持

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `backend/plugins/_template/plugin.py`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/plugin_development.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト61件成功、Plugin UIテスト27件成功、ガイド・雛形テスト2件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.24 動的プラグインUI拡張 Phase 7（2026-07-17）

- UIスキーマをversion 8へ更新し、form fieldへJSON booleanを扱う `checkbox` を追加
- checkbox定義を `type` / `id` / `label` / `required` / `value` の5属性だけに限定
- 初期valueと送信値をboolだけに制限し、`type(value) is bool` でPythonのbool/intを厳密分離
- required checkboxはTrueを必須とし、False、0 / 1、文字列、nullをhandler呼出前に拒否
- 既存text/textarea/selectは文字列payloadを維持し、version 7定義との互換性を保持
- フロントはDOM APIでcheckboxを構築し、checkedプロパティからboolean値を収集
- 既存formの送信中無効化・復帰、feedback、CustomEvent、status更新処理を混在fieldでも共通利用
- コピー用雛形へcheckbox例を追加し、開発ガイド・設計書・backlogをversion 8へ同期
- number、secrets連携、checkbox group、field単位disabledは後続タスクとして維持

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `backend/plugins/_template/plugin.py`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/plugin_development.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト63件成功、Plugin UIテスト29件成功、ガイド・雛形テスト2件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.25 動的プラグインUI拡張 Phase 8（2026-07-17）

- UIスキーマをversion 9へ更新し、form fieldへ `number` を追加
- optional空値をnull、required送信値を有限number必須として定義
- bool、文字列、NaN、Infinity、±1e15超過を定義・payloadの両方で拒否
- min/maxと初期値・送信値の範囲をhandler呼出前に検証
- フロントはinput number、step any、valueAsNumberを使用し、空欄をnullとして送信
- 既存text/textarea/select/checkbox契約とversion 8互換を維持
- 雛形、開発ガイド、設計書、backlogをversion 9へ同期

**変更ファイル**: `backend/plugins/plugin_manager.py`, `backend/main.py`, `backend/plugins/_template/plugin.py`, `frontend/js/plugin-ui.js`, `tests/test_regressions.py`, `document/plugin_development.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト65件成功、Plugin UIテスト31件成功、ガイド・雛形テスト2件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.26 動的プラグインUI拡張 Phase 9（2026-07-17）

- UIスキーマをversion 10へ更新し、参照専用の `secret` fieldを追加
- 実値は既存の `POST /api/secrets/register` だけに送信し、通常のPlugin UI actionとhandlerには `{{secret:N}}` 参照だけを渡す
- optional secretはnull、required secretは登録済み完全一致参照を必須とし、不明・手書き・形式不正参照をhandler呼出前に拒否
- PluginManagerへvalidatorを注入してsecrets実装との直接依存を避け、secrets無効時は参照を受理しない
- フロントはDOM APIで登録dialogとマスク済み状態を構築し、dialog close/finallyで実値入力をクリア。feedback、CustomEvent、dataset、action payloadに実値を保存しない
- 既存secretsの `normalize` APIも実値を保護処理できるが、secret fieldはregister APIだけを使用する
- secret一覧・選択・reveal・自動復元・handlerへの実値解決は対象外として維持

**変更ファイル**: `.gitignore`, `backend/main.py`, `backend/plugins/plugin_manager.py`, `frontend/js/plugin-ui.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/plugin_development.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 回帰テスト66件成功、Plugin UIテスト32件成功、ガイド・雛形テスト2件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.27 状態履歴と編集・削除・再生成の整合性（2026-07-17）

- 正常完了したSTATEだけを {session_id}_state_history.jsonl に会話境界ごと記録する
- 編集・削除・truncate・再開では、残った履歴以下で最新の状態へ復元し、後続履歴を破棄する
- STATEなし・中断・エラーは記録せず、状態本体・副履歴は原子的に更新する
- チャットUIは編集・削除・再生成後に状態APIを再読込する

**変更ファイル**: `backend/main.py`, `frontend/js/chat.js`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`

**確認結果**: 回帰テスト67件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.28 状態更新の信頼性と初期state（2026-07-17）

- SOUL.mdの `#` / `## 開始時の状況` 節を新規セッションの初期stateとしてseedし、message_count 0のsnapshotを記録
- 通常のSTATE更新を既存stateへのmerge方式へ変更し、モデルが言及しなかった項目を保持
- `- 項目名: [解決]` だけを明示的削除として扱い、全項目解決後の空stateも有効なsnapshotとして保存
- STATEが2回連続で欠落した場合は次回以降のプロンプトへ再確認指示を追加し、追加LLM呼び出しは行わない
- merge後またはseedが4,096文字を超える場合は自動切り詰めせず更新を拒否し、通常更新では直前stateを維持して次回1回だけ整理指示を追加
- 欠落回数と上限超過指示を新規開始、再開、ペルソナ切替、履歴復元でリセットし、キャンセル・上限超過を欠落回数へ加算しない
- 初回実装 `51187a2` のmerge、空snapshot、セッション境界、seed上限の不整合を再レビューで検出し、`f6de4fd` で修正
- P3テストをソース文字列の存在確認から、merge・明示解決・空state・overflow・境界リセット・seed上限の振る舞い検証へ更新

**変更ファイル**: `backend/main.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: P1/P3対象テスト7件成功、全回帰73件成功、Python構文チェック成功、`git diff --check` 問題なし

### 22.29 P2 Phase 3 persona基本情報の派生索引（2026-07-17）

- `SOUL.md` / `SKILL.md` / `style.yaml` が揃った完全なpersonaを、保存・更新・import完了後に `kind=persona_base` としてChromaDBへ索引化
- 追加LLM呼び出しは行わず、確定ファイルをNFKC・改行正規化してsource別の決定的ID、3ファイル共通の `source_hash`、revisionを生成
- 更新時は同一personaの旧 `persona_base` を削除して現在内容へ置換し、通常会話検索は引き続き `session_fact` のみに限定
- Memory未設定、不完全persona、索引障害でもpersona本体の保存/importは成功し、再構築可能なwarningを返す
- `POST /api/memory/personas/{persona_id}/rebuild` を追加し、正本ファイルから派生索引を手動再構築可能にした

**変更ファイル**: `backend/main.py`, `backend/plugins/memory/plugin.py`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 全回帰85件成功、Python構文チェック成功、`git diff --check` 問題なし

### 22.30 P2 Phase 4 persona削除ライフサイクル統合（2026-07-17）

- 削除前preview APIを追加し、persona定義、sessions、session-log、draftの件数とactive状態を本文非公開で返す
- active personaは自動切替せず409で拒否し、明示的な切替を要求
- inactive persona削除をAPI lock内でsessions、state/meta sidecar、session-log、対象current-session、draft、全kindのMemory、persona定義へ統合
- 資源別結果、`deleted_count`、`ok/partial/error`、部分失敗時のretry契約により冪等再実行を可能にした
- Studio UIはpreview件数を確認表示し、partial/error時は一覧を維持して再試行可能にした

**変更ファイル**: `backend/main.py`, `frontend/js/studio.js`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: persona削除対象テスト3件成功、全回帰88件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.31 Persona Studio保存後の表示維持（2026-07-17）

- persona保存成功後もSOUL.md/SKILL.mdの結果パネルと修正・保存・テスト会話の操作領域を表示したまま維持
- 保存後の編集可能状態を保持し、保存済みpersona一覧だけをawait再読込
- persona_base索引warningは保存成功を維持したまま、再構築可能な警告として画面へ表示
- 新規生成、保存済みpersona読込、生成前フォーム下書き読込の表示契約を回帰テストで分離確認

**変更ファイル**: `frontend/js/studio.js`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: Persona Studio表示契約テスト2件成功、全回帰90件成功、JavaScript・Python構文チェック成功、`git diff --check` 問題なし

### 22.32 チャット送信・停止UIの統一（2026-07-17）

- 送信ボタンと別位置の停止ボタンを廃止し、同一のsend-btnを通常時「送信」・SSE応答中「停止」へ切替
- 通常送信、再生成、ユーザー発言編集後送信を共通busy guardで二重送信防止し、完了・エラー・中断を共通finallyで復帰
- 停止要求の多重送信を防ぎ、サーバー保存猶予後も上流が応答しない場合だけAbortControllerで中断
- chat cancel eventのclearを要求開始直後へ移し、hook/context構築中に届いた停止要求がストリーム直前のclearで失われる競合を解消
- 停止状態のsend-btnを赤色表示し、テキストとaria-labelを言語設定に沿って切替

**変更ファイル**: `backend/main.py`, `frontend/index.html`, `frontend/js/chat.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: 送信・停止契約テスト3件成功、全回帰93件成功、Python・JavaScript構文チェック成功、`git diff --check` 問題なし

### 22.33 P6 Setup・Studio・状態パネルのレイアウト調整（2026-07-17）

- Setupのpersonaカードを内容高に揃え、余白をカード間へ集約。520px以下では1列表示として横切れを防止
- Studioの複数入力fieldを狭幅時に縦積みし、タブとテスト入力の最小幅を解除
- Chatのヘッダー文字列を省略表示し、入力欄へ `min-width: 0` を設定して送信・機密ボタンを含む横切れを防止
- 状態パネルは入力欄直上のDOM順を維持し、狭幅時は最大 `min(40vh, 240px)` まで上方向へ拡張
- 500px幅のSetup実画面で、カードの内容高、1列表示、左右余白、下部ボタンを確認

**変更ファイル**: `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: レスポンシブ契約テスト3件成功、全回帰96件成功、500px実画面確認、Python構文チェック成功、`git diff --check` 問題なし

### 22.34 P7 Memory DB管理画面（2026-07-17）

- metadata-onlyの `GET /api/memory/records` を追加し、id/kind/persona/session/source/orphanだけを返却。documentとembeddingは取得・公開しない
- `POST /api/memory/delete` を追加し、all/persona/session/records/orphansの5 scopeを単一契約で管理
- scope別の必須・禁止引数を厳密検証し、recordsは1〜500件・重複なし、session IDは8桁を要求
- 削除はAPI lock内で実行し、孤児は実行時点の正本session一覧から再判定。0件の再実行も成功する冪等契約
- SettingsへMemory DBタブを追加し、kind別・persona別・孤児統計、metadata一覧、persona/session/kind filterを表示
- 選択/persona/session/孤児/全件削除は対象件数をconfirmへ明示し、実行中無効化と完了後再読込を統一
- record表はcreateElement/textContent/replaceChildren/addEventListenerだけで構築し、CSP/XSS契約を維持
- 900px/500px実画面と、空の実ChromaDBに対するstats/records/全件削除、不正引数422を確認

**変更ファイル**: `backend/main.py`, `backend/plugins/memory/plugin.py`, `frontend/settings.html`, `frontend/js/settings.js`, `frontend/css/style.css`, `tests/test_regressions.py`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: Memory管理対象テスト19件成功、全回帰102件成功、Python・JavaScript構文チェック成功、実APIでstats/records/冪等全件削除・不正引数422を確認、900px/500px実画面確認、git diff --check 問題なし

### 22.35 Phase A 利用開始・Persona import契約の整合化（2026-07-17）

- ルート`bootstrap.py`を追加し、Windows/macOS/Linux launcherをPython 3.11以上・専用`.venv`・初回依存導入・config初回コピーの共通契約へ統一
- 既存`.venv`と`backend/config.yaml`は自動更新・上書きせず、空configも保護して明示エラーで停止
- Windows launcherとdefault configの`E:/LLM`固定値を廃止し、ChromaDBは相対path、Hugging Face cacheは標準設定または利用者の環境変数を尊重
- Persona importをPI-Bへ確定し、`SOUL.md` / `SKILL.md` / `style.yaml`の不足・空・不正schemaを登録前に拒否
- validate APIは`complete` / `incomplete` / `invalid`、import APIは不足/不正422・既存ID 409・完成成功200の契約へ統一
- importは一時ディレクトリでコピー後に再検証し、失敗時にdestinationを残さず、完成確定後だけ`persona_base`を索引化
- Studio UIは検証成功まで登録を無効化し、不足・不正・衝突・索引warningを区別して日英表示
- README、設計書、backlogをQS-A + PI-B契約へ同期

**変更ファイル**: `bootstrap.py`, `start_server.bat`, `start_server.sh`, `backend/config.default.yaml`, `backend/core/config.py`, `backend/main.py`, `frontend/studio.html`, `frontend/js/studio.js`, `frontend/js/i18n.js`, `tests/test_regressions.py`, `README.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: Phase A対象テスト10件成功、全回帰111件成功、Python構文・compileall成功、JavaScript 7ファイル構文チェック成功、pip check問題なし、標準`start_server.bat`実サーバーで主要5画面200・validate complete・既存ID import 409・config hash/mtime不変を確認、停止後port 8765解放、git diff --check問題なし

### 22.36 Phase B chat/session/security契約の整合化（2026-07-18）

- `POST /api/chat`をstrictな`ChatRequest`へ接続し、text 1〜8,000文字、persona/session ID形式、boolの`resend`、余分なfield拒否をPydantic validationへ統一
- chat JSON bodyを16,384 bytes以下に制限し、超過413、field違反422、session preflight失敗409、受付後SSE維持の境界を確定
- auto-resumeと明示resumeを対象persona/session/pathの存在・short ID一意性・JSONL読込のpreflight後にだけ`on_session_end`→activation→`on_session_start`へ進む順序へ変更
- `GET /api/session/history`からauto-resume副作用を除去し、不一致409→UIが明示resume→GETを1回だけ再試行する契約へ変更
- `/api/`配下のPOST・PUT・PATCH・DELETE全39 routeを共通same-origin middlewareで保護。loopback Host限定、cross-originとcross-siteを403、Origin欠落ローカルclientを条件付き互換とした
- 通常送信・再生成のSSE実処理、16,384/16,385 bytes境界、preflight失敗時の無副作用、成功時hook順、39 route横断403を回帰テストへ追加
- README、設計書、backlogをPhase BのAPI・UI・security契約へ同期（version体系の正規化はPhase Dへ維持）

**変更ファイル**: `backend/main.py`, `backend/core/history.py`, `frontend/js/chat.js`, `tests/test_regressions.py`, `README.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`

**確認結果**: Phase B対象テスト44件成功、全回帰120件成功、Python構文・py_compile・compileall成功、JavaScript 7ファイル構文チェック成功、pip check問題なし、標準`start_server.bat`実サーバーで主要5画面200・chat validation 422・body超過413・cross-origin 403・Origin欠落200・履歴不一致409・config hash不変を確認、停止後port 8765解放、`git diff --check`問題なし

### 22.37 Phase C 旧session互換保証の撤回（2026-07-20）

- §11.1の「旧`YYYY-MM-DD.jsonl`も互換維持」という記述は、初期実装からlist・resume・deleteが新形式だけを扱っており、実装事実と一致していなかったため撤回
- workspaceの`sessions/`と`session-log/`をread-onlyで確認し、旧形式・新形式ともに実データ0件を確認
- ユーザー判断により旧形式のruntime互換、migration tool、移行手順を提供しない
- 正規形式を`sessions/{persona_id}/YYYY-MM-DD_HHMMSSRR.jsonl`だけに確定
- 起動時の自動migration・自動削除は追加せず、runtime codeとユーザーデータは変更しない
- 未使用`History._load_latest()`の整理はPhase Dの未使用コード監査へ継続

**変更ファイル**: `README.md`, `document/RPスタンドアロンアプリ_設計書.md`, `document/CHANGELOG.md`, `document/backlog.md`, `tests/test_regressions.py`

**確認結果**: Phase C対象3件成功、正本`requirements.txt`を解決したuv隔離環境で全回帰123件成功、Python構文・compileall成功、JavaScript 7ファイル構文チェック成功、隔離環境のpip check問題なし、session file 0件・port 8765解放・`git diff --check`問題なし。既存system/workspace Pythonには依存version driftがあるため変更せず、隔離環境で正本契約を検証

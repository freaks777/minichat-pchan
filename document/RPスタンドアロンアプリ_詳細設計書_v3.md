
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

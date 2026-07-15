# Backlog — RPスタンドアロンアプリ

> 現行仕様は `RPスタンドアロンアプリ_設計書.md` を参照してください。
> 変更履歴は `CHANGELOG.md` を参照してください。

---

## 優先度順タスク（2026-07-16 更新）

### 1. 短い機密値の自動置換除外

- `plugin.py:protect_text()` は1文字でも登録値と一致すれば全文置換する
- イニシャル等を登録した場合に通常文章が大量置換される
- 修正: 2文字以下の値は `protect_text()` の自動置換対象外にする
- 影響範囲: `backend/plugins/secrets/plugin.py` 1ファイル + テスト追加

### 2. CSP/XSS 監査と対策（5段階）

1. ユーザー入力・API応答が到達するDOM挿入箇所を監査（優先: `studio.js:590` のAPIエラー挿入）
2. 危険な `innerHTML` をDOM構築または `textContent` に変更
3. インラインイベント（`onclick`, `onchange` 等）を `addEventListener` に移行
4. report-only 相当でCSP違反を確認
5. CSP を正式適用

### 3. 機密ファイル権限制限

- Linux/macOS: `os.chmod` で所有者のみ読み取り可能に
- Windows: ユーザーACLで十分なため別対応
- 既存ファイルの権限更新方針も合わせて検討

### 4. 長期運用改善

| 項目 | 詳細 | 参照 |
|------|------|------|
| memory 重複記憶抑制 | 同一factの重複保存防止 | 将来修正候補 #3 |
| httpx.AsyncClient 再利用 | KeepAlive/TCP再利用が効かない問題 | 将来修正候補 #4 |
| 動的プラグインUI基盤 | `get_ui_slot()` の実運用化 | — |

### 5. 設計・UI改善

- watchdog 汎用化（メール以外の通知手段）
- ボタン配置の整理・視認性改善
- チャット画面へのログ取得機能追加

### 6. 将来プラグイン

| プラグイン | 概要 |
|-----------|------|
| voice | TTS/STT。VOICEVOX / faster-whisper。hooks: `on_user_message`, `on_response_complete` |
| image_gen | 状況画像生成。`get_ui_slot()` でボタン追加 |
| quick_actions | クイックアクションボタン。固定＋動的提案、キャラごとYAML管理 |

---

## 将来修正候補（低優先・現状問題なし）

| # | 内容 | 条件 |
|---|------|------|
| 2 | `History._load_latest()` で破損JSONLのrole並び検証がない | ファイル破損の報告時 |
| 3 | memory プラグインの重複記憶抑制 | 長期運用で検索精度低下時 |
| 4 | HTTPクライアント再利用（`httpx.AsyncClient`） | 高頻度API呼び出し時 |

---

## アーキテクチャ検討（未確定）

- プラグイン間依存関係の順序制御
- フロントプラグインUI動的追加の実装方式
- モバイル対応（`0.0.0.0` + 認証 + レスポンシブレイアウト）

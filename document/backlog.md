# Backlog — RPスタンドアロンアプリ

> 現行仕様は `RPスタンドアロンアプリ_設計書.md` を参照してください。
> 変更履歴は `CHANGELOG.md` を参照してください。

---

## 優先度順タスク（2026-07-16 更新）

### 1. CSP/XSS 監査と対策（完了）

1. ✅ DOM挿入箇所の監査完了（F1〜F5を特定）
2. ✅ `innerHTML` のDOM構築化 — フロントエンドJavaScriptから全廃
3. ✅ インラインイベント／インラインscriptの廃止 — 外部JavaScriptの `addEventListener` へ移行済み
4. ✅ CSP Report-Onlyを導入・実測 — 5画面で `style-src-attr` 87件（Studio 73 / setup 6 / settings 4 / chat 3 / sessions 1）、その他の違反0件
5. ✅ インラインstyle 87件をCSS classへ移行し、再実測0件を確認後にCSPを正式適用

### 2. 長期運用改善

| 項目 | 詳細 | 参照 |
|------|------|------|
| memory 重複記憶抑制 | 同一factの重複保存防止 | 将来修正候補 #3 |
| httpx.AsyncClient 再利用 | KeepAlive/TCP再利用が効かない問題 | 将来修正候補 #4 |
| 動的プラグインUI基盤 | `get_ui_slot()` の実運用化 | — |

### 3. 設計・UI改善

- watchdog 汎用化（メール以外の通知手段）
- ボタン配置の整理・視認性改善
- チャット画面へのログ取得機能追加

### 4. 将来プラグイン

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

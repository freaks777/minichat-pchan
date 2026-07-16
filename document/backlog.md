# Backlog — RPスタンドアロンアプリ

> 現行仕様は `RPスタンドアロンアプリ_設計書.md` を参照してください。
> 変更履歴は `CHANGELOG.md` を参照してください。

---

## 優先度順タスク（2026-07-17 更新）

### 1. CSP/XSS 監査と対策（完了）

1. ✅ DOM挿入箇所の監査完了（F1〜F5を特定）
2. ✅ `innerHTML` のDOM構築化 — フロントエンドJavaScriptから全廃
3. ✅ インラインイベント／インラインscriptの廃止 — 外部JavaScriptの `addEventListener` へ移行済み
4. ✅ CSP Report-Onlyを導入・実測 — 5画面で `style-src-attr` 87件（Studio 73 / setup 6 / settings 4 / chat 3 / sessions 1）、その他の違反0件
5. ✅ インラインstyle 87件をCSS classへ移行し、再実測0件を確認後にCSPを正式適用

### 2. 追加課題

| 順位 | 項目 | 次の対応 |
|-----:|------|----------|
| 1 | P6 Setup・Studio・状態パネルのレイアウト | 実画面で選択画面、スタイル、自由設定、上方向状態パネルをまとめて確認・調整 |
| 2 | P7 Memory DB管理画面 | 完成したP2管理APIを使い、統計、preview、全件/persona/session/個別削除、孤児管理UIを追加 |

P2 セッション削除とMemory DBの整合性はPhase 0〜4（一覧、schema/内部API、session削除、persona_base、persona削除）まで完了。
P4 Persona Studio保存後の表示維持は完了。保存後も編集結果と操作領域を維持し、保存済み一覧だけを更新する。
P5 チャット送信・停止UIは完了。単一ボタンを送信/停止で切り替え、全送信経路と停止後復帰を統一する。

### 3. 長期運用改善

| 項目 | 詳細 | 参照 |
|------|------|------|
| memory依存の定期更新 | 4パッケージを一体で更新し、クリーンvenv・実モデル・通常起動を再検証 | `requirements.txt` |

### 4. 設計・UI改善

- watchdog 汎用化（メール以外の通知手段）
- ボタン配置の整理・視認性改善
- チャット画面へのログ取得機能追加

### 5. 将来プラグイン

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
| 3 | memory の意味的重複・別セッション間統合 | ペルソナスコープ検索で類似記憶が増えた時 |

---

## アーキテクチャ検討（未確定）

- プラグイン間依存関係の順序制御
- モバイル対応（`0.0.0.0` + 認証 + レスポンシブレイアウト）

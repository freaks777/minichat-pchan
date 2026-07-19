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

### 2. 全体整合性修正

1. ✅ **Phase A（P1）**: Quick Start/config/venv契約とPersona Studio import契約をQS-A + PI-Bで実装・検証・commit済み（`7c63cbf`）
2. ✅ **Phase B（P2-high）**: `/api/chat`入力契約、auto-resume副作用順序、mutating API same-origin統一を実装・検証・commit済み（`72964c4`）
3. ✅ **Phase C（P2）**: 旧session実データ0件を確認し、互換保証撤回・migrationなしへ文書契約を訂正。対象3件・全回帰123件成功、Hermes最終承認済み
4. ⏳ **Phase D（P2/P3）**: version体系、README・設計書・CHANGELOG、debug tool、未使用コード、State Tracking通しテストを整理

P2 セッション削除とMemory DBの整合性はPhase 0〜4（一覧、schema/内部API、session削除、persona_base、persona削除）まで完了。
P4 Persona Studio保存後の表示維持は完了。保存後も編集結果と操作領域を維持し、保存済み一覧だけを更新する。
P5 チャット送信・停止UIは完了。単一ボタンを送信/停止で切り替え、全送信経路と停止後復帰を統一する。
P6 Setup・Studio・状態パネルのレイアウトは完了。Setupカードを内容高へ揃え、狭幅時は1列化し、Studioの複数入力列を縦積み、Chatのヘッダー・入力欄の横切れを防止する。状態パネルは入力欄直上を維持し、最大40vhまで上方向へ拡張する。
P7 Memory DB管理画面は完了。metadata-only統計・一覧と、選択/persona/session/孤児/全件のscope別削除をSettingsから操作できる。

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

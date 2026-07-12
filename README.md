# minichat-pchan

> ⚠️ **開発中 / Under Development** — 破壊的変更が入る可能性があります。Breaking changes may occur.

AIキャラクターとチャットするアプリ。`SOUL.md` に人格を書き、複数ペルソナを切り替えて会話できる。

Chat with AI characters. Write their personality in a `SOUL.md` file, switch between multiple personas.

## Why

AIとのロールプレイ用アプリは多数あるが、キャラクター定義の自由度やLLMプロバイダの選択肢に制約がある場合が多い。`SOUL.md` というシンプルなファイル1つでキャラを作り、好きなプロバイダで動かせる自分用のアプリが欲しかった。

Many roleplay apps exist, but some limit character customization or tie users to specific services. I wanted a simple app where one `SOUL.md` file defines a character, running on any LLM provider of your choice.

## Features

- **SOUL.md + SKILL.md** — キャラクターの人格・知識をマークダウンで定義
- **マルチペルソナ** — 複数キャラを登録・切替可能
- **プラグイン機構** — secrets（機密情報マスク）/ memory（長期記憶）/ watchdog（離席監視）/ mail（メール通知）/ session_log 他
- **8プロバイダ対応** — OpenRouter / OpenAI / Anthropic / Google / xAI / DeepSeek / GLM / OpenCode
- **SPA フロントエンド** — チャット・セッション管理・ペルソナスタジオ・設定画面

## Quick Start

### 1. 環境変数

`.env.example` をコピーして `.env` を作成し、使用するAPIキーを設定。

```bash
cp .env.example .env
```

### 2. 設定

`backend/config.default.yaml` をコピーして `config.yaml` を作成（デフォルト値で良ければコピー不要、自動生成）。

### 3. 起動

```bash
# Windows
start_server.bat

# macOS / Linux
bash start_server.sh
```

ブラウザで `http://localhost:8765` を開く。

### 4. ペルソナ作成

`personas/` に新しいディレクトリを作り、`SOUL.md`（人格定義）と `SKILL.md`（知識・能力）を配置。`_template/` を参考に。

## Project Structure

```
├── backend/
│   ├── core/           # API / 設定 / 履歴 / ペルソナ管理
│   ├── plugins/        # プラグイン（secrets, mail, watchdog, memory, ...）
│   ├── main.py         # FastAPI エントリポイント
│   └── config.default.yaml
├── frontend/
│   ├── index.html      # チャット画面
│   ├── sessions.html   # セッション履歴
│   ├── settings.html   # 設定
│   ├── studio.html     # ペルソナスタジオ
│   ├── css/
│   └── js/
├── personas/
│   ├── _template/      # ペルソナ作成テンプレート
│   └── kyouka-detective/  # サンプルペルソナ
└── document/           # 設計書
```

## Requirements

- Python 3.11+
- 対応プロバイダのAPIキー

### 必須パッケージ

```bash
pip install fastapi uvicorn httpx pyyaml python-dotenv
```

### オプション

| パッケージ | 用途 |
|---|---|
| `chromadb` | memory プラグイン（長期記憶・ベクトル検索） |
| `sentence-transformers` | memory プラグイン（埋め込みモデル） |

## Data & Storage

すべてファイルベース。外部DBサーバー不要。

| データ | 保存先 | 形式 |
|---|---|---|
| 会話履歴 | `sessions/*.jsonl` | JSONL |
| 会話ログ | `session-log/*.md` | Markdown |
| 長期記憶 | ChromaDB（`chroma.path`） | ベクトルDB |
| 機密情報 | `data/secrets_store.json` | JSON |
| ペルソナ | `personas/*/` | Markdown + YAML |

ChromaDB の保存先と埋め込みモデルは `config.yaml` の `chroma` セクションで変更可能。

## Third-party Content

ユーザーが作成・導入するペルソナおよびプラグインは自己責任で使用してください。悪意あるファイルによる被害について作者は責任を負いません。

Third-party personas and plugins are used at your own risk. The author is not responsible for damages caused by malicious files.

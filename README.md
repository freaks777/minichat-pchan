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
- **Persona Studio** — フォーム入力・テキスト抽出・テスト会話によるペルソナ作成支援
- **プラグイン機構** — secrets（機密情報マスク）/ memory（長期記憶）/ watchdog（離席監視）/ mail（メール通知）/ session_log 他
- **マルチプロバイダ** — OpenAI互換APIを中心に複数プロバイダ対応（詳細は Supported Providers 参照）
- **SPA フロントエンド** — チャット・セッション管理・ペルソナスタジオ・設定画面

## Quick Start

Python 3.11以上をインストールしてください。標準起動スクリプトが初回だけ専用`.venv`、依存パッケージ、`backend/config.yaml`を準備します。

### 1. 環境変数

`.env.example` をコピーして `.env` を作成し、使用するAPIキーを設定します。APIキーを後で設定する場合もサーバーは起動できますが、LLM呼び出しは失敗します。

```bash
cp .env.example .env
```

### 2. 起動

```bash
# Windows
start_server.bat

# macOS / Linux
bash start_server.sh
```

初回起動では次を自動実行するため、依存ダウンロードに数分以上かかる場合があります。

1. `.venv` がなければ作成
2. 新規`.venv`へ`requirements.txt`をインストール
3. `backend/config.yaml`がなければ`backend/config.default.yaml`から初回コピー

既存の`.venv`は再作成・自動更新せず、既存の`backend/config.yaml`は上書きしません。空のconfigが存在する場合も上書きせず、復旧手順を表示して停止します。Hugging Face cacheを変更したい場合は、`HF_HOME` / `SENTENCE_TRANSFORMERS_HOME`を利用者側で設定してください。

起動時は専用`.venv`のpackage metadataを`requirements.txt`とread-onlyで照合します。依存drift、不足package、検査不能を検出した場合は警告とrepairコマンドを表示しますが、`.venv`を自動更新せずサーバー起動を継続します。案内は専用venvにpipがあれば`<venv-python> -m pip install -r requirements.txt`、pipがなくuvを利用できれば`uv pip install --python <venv-python> -r requirements.txt`を使用します。実行するかは利用者が判断してください。

ブラウザで `http://localhost:8765` を開きます。

### ローカルAPIの入力・オリジン契約

- `POST /api/chat` のJSON bodyは16,384 bytes以下、`text`は前後空白除去後1〜8,000文字です。`persona_id`と`session_id`は規定形式、`resend`は真偽値だけを受け付け、余分なfieldは拒否します。
- body超過は413、fieldの型・形式・文字数違反は422、指定sessionを安全に復元できない場合は409です。受付後の応答は従来どおりSSEです。
- `/api/`配下のPOST・PUT・PATCH・DELETEはloopbackのsame-originだけを受け付け、cross-originは403にします。Originを送らないローカルCLIは、Hostが`127.0.0.1` / `localhost` / `::1`でcross-site指定がない場合に限り利用できます。
- 履歴GETは状態を変更しません。表示対象がactive sessionと異なる場合、UIは明示的な`POST /api/session/resume`成功後に履歴GETを1回だけ再試行します。

### 3. 設定

初回生成された`backend/config.yaml`を必要に応じて編集します。起動スクリプトを使わず手動準備する場合は、次を実行してください。

```bash
python -m venv .venv

# Windows
.venv/Scripts/python.exe -m pip install -r requirements.txt
copy backend/config.default.yaml backend/config.yaml

# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
cp backend/config.default.yaml backend/config.yaml
```

### 4. ペルソナ作成

`personas/` に新しいディレクトリを作り、`SOUL.md`（人格定義）、`SKILL.md`（知識・能力）、`style.yaml`（文体設定）を配置します。`personas/_template/`を参考にしてください。Persona Studioのファイルimportも3ファイルすべてを必須とし、不足・空・不正形式は登録前に拒否します。

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

### インストール

通常はQuick Startの起動スクリプトが専用`.venv`へ自動インストールします。手動導入する場合もsystem Pythonへ直接入れず、上記の`.venv`を使用してください。

`requirements.txt` にはコア機能とmemoryプラグインの検証済み依存関係が含まれます。

### 主な追加パッケージ

| パッケージ | 用途 |
|---|---|
| `chromadb` | memory プラグイン（長期記憶・ベクトル検索） |
| `sentence-transformers` | memory プラグイン（埋め込みモデル） |
| `transformers` / `huggingface-hub` | 埋め込みモデルの互換依存関係 |

## Supported Providers / 対応プロバイダ

`backend/config.default.yaml` に全プロバイダの設定が含まれているが、APIキーを取得していないものは**動作未検証**。

All providers are configured in `backend/config.default.yaml`, but those without a verified API key are **untested**.

Status は作者環境での動作確認状況です。✅ = 実稼働確認済み、⚠️ = コード対応済みだがAPIキー未取得のため未検証。

Status indicates verification in the author's environment: ✅ = tested, ⚠️ = implemented but API key not available for testing.

| Provider | Interface | Status |
|---|---|---|
| OpenRouter | OpenAI-compatible | ✅ Verified |
| OpenCode Go | OpenAI-compatible | ✅ Verified |
| OpenCode Zen | OpenAI-compatible | ⚠️ Not tested (requires API key) |
| OpenAI | OpenAI-compatible | ⚠️ Not tested (requires API key) |
| Anthropic | Anthropic API | ⚠️ Not tested (requires API key) |
| Google | Gemini API | ⚠️ Not tested (requires API key) |
| xAI | OpenAI-compatible | ⚠️ Not tested (requires API key) |
| DeepSeek | OpenAI-compatible | ⚠️ Not tested (requires API key) |
| GLM | OpenAI-compatible | ⚠️ Not tested (requires API key) |

未検証プロバイダの動作保証はありません。APIキーを設定すればコード上の対応は完了しており、動く可能性は高いですが、実際のリクエスト疎通までは確認していません。

Untested providers have no guarantee of working. The code path exists and should work once an API key is set, but end-to-end request flow has not been verified.

## Data & Storage

すべてファイルベース。外部DBサーバー不要。

| データ | 保存先 | 形式 |
|---|---|---|
| 会話履歴 | `sessions/{persona_id}/YYYY-MM-DD_HHMMSSRR.jsonl` | JSONL |
| セッションメタデータ | `sessions/{persona_id}/YYYY-MM-DD_HHMMSSRR.meta.json` | JSON |
| 現在状態 | `sessions/{persona_id}/HHMMSSRR_state.json` | JSON |
| 状態履歴 | `sessions/{persona_id}/HHMMSSRR_state_history.jsonl` | JSONL |
| 会話ログ | `session-log/{persona_id}/YYYY-MM-DD_HHMMSSRR.md` | Markdown |
| 長期記憶 | ChromaDB（`chroma.path`、既定値`data/chroma`） | ベクトルDB |
| 機密情報 | `data/secrets_store.json` | JSON |
| ペルソナ | `personas/{persona_id}/` | Markdown + YAML |

会話履歴の対応形式は`YYYY-MM-DD_HHMMSSRR.jsonl`だけです。旧`YYYY-MM-DD.jsonl`は互換・migration対象ではありません。アプリは旧形式を一覧・再開・削除せず、起動時の自動migrationや自動削除も行いません。

ChromaDB の保存先と埋め込みモデルは `config.yaml` の `chroma` セクションで変更可能。起動時は埋め込みproviderだけを準備し、DBはMemory API・検索・保存を初めて使う時に開きます。診断・隔離テストではプロセス環境変数 `RP_CHROMA_PATH` が `chroma.path` より優先され、設定ファイルを変更せず別DBを利用できます。Memoryレコードはmetadataの`kind`で`session_fact`（会話由来）、`persona_base`（SOUL.md / SKILL.md / style.yaml由来）、`legacy`（kind未設定の旧レコード）を区別します。

## API Debug Dump

`backend/debug_dump_api.py`は、設定済みOpenCode Go / DeepSeek系APIの生レスポンスを再現確認する開発用CLIです。通常起動には不要です。

```bash
# 簡易リクエストと抽出リクエスト
.venv/Scripts/python.exe backend/debug_dump_api.py

# 指定テキストで抽出リクエスト
.venv/Scripts/python.exe backend/debug_dump_api.py --extract "確認するテキスト"
```

macOS / Linuxでは`.venv/bin/python`を使用します。実行すると外部LLM APIを呼び出すため利用料金が発生し得ます。ダンプは`backend/logs/api_debug/`へ保存され、request/response本文を含む場合があります。共有・commit前に機密情報や個人情報を確認してください。

## Disclaimer

本ソフトウェアは「現状有姿」で提供され、明示・黙示を問わず一切の保証を伴いません。本ソフトウェアの使用により生じた損害・API使用料・データ消失について作者は責任を負いません。

This software is provided "as is", without warranty of any kind. The author is not liable for any damages, API usage fees, or data loss arising from its use.

## Third-party Content

ユーザーが作成・導入するペルソナおよびプラグインは自己責任で使用してください。悪意あるファイルによる被害について作者は責任を負いません。

Third-party personas and plugins are used at your own risk. The author is not responsible for damages caused by malicious files.

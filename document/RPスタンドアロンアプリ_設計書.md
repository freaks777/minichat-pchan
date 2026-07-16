# RPスタンドアロンアプリ 設計書 v3.11

> **このファイルが正本です。** 現行コードが満たすべき仕様のみを記載しています。
> 変更履歴は `CHANGELOG.md`、未実装・保留項目は `backlog.md` を参照してください。
> 旧統合設計書は `archive/` にアーカイブされています。

作成: 2026-06-30
最終更新: 2026-07-16 (v3.14)
ベース: 旧 `RPスタンドアロンアプリ_詳細設計書_v3.md` からの再構成

---

## 1. 設計方針の整理

v2では機能を全部並列に詰め込んで肥大化したため、**コア**と**プラグイン**を明確に分離する。

- **コア**: テキストチャットの会話ループだけ。これだけで完結して動く最小単位
- **基本セットプラグイン**: 長期記憶、コスト管理、watchdogなど、「ほぼ全員使うだろう」機能群。ただし構造上はコアに依存しない外付けモジュール
- **将来プラグイン**: 音声（TTS/STT）、画像生成、動的クイックアクション提案など、優先度が低い／後から実装する機能。設計だけ残し実装は後回し

この分離により、コアさえ動けば最低限のRPが成立し、プラグインは要不要・実装順序を自由に決められる。

---

## 2. アーキテクチャ全体方針（変更なし）

**Tauri（フロントシェル）+ Python backend（FastAPI）のハイブリッド構成**

```
┌─────────────────────────────────────────┐
│  Tauri (Rust shell, 軽量ウィンドウ管理)    │
│  └─ フロントエンド (HTML/CSS/JS)           │
│     - チャットUI（コア）                   │
│     - プラグインが追加するUI要素は動的に挿入 │
└────────────────┬──────────────────────────┘
                 │ HTTP (SSE)
┌────────────────▼──────────────────────────┐
│  Python Backend (FastAPI)                  │
│  ┌─────────────────────────────────────┐  │
│  │ コア                                  │  │
│  │  - main.py（会話ループ・SSE）     │  │
│  │  - api.py（API呼び出し）               │  │
│  │  - history.py（履歴管理）              │  │
│  │  - config.py（設定読込）               │  │
│  └─────────────────────────────────────┘  │
│  ┌─────────────────────────────────────┐  │
│  │ PluginManager（hook機構）              │  │
│  └──────────────┬──────────────────────┘  │
│  ┌───────────────▼─────────────────────┐  │
│  │ plugins/                             │  │
│  │  [基本セット]                         │  │
│  │   - watchdog/                        │  │
│  │   - memory/（長期記憶）                │  │
│  │   - cost/（コスト管理）                │  │
│  │   - session_log/（後処理）             │  │
│  │   - persona_studio/（ペルソナ作成支援） │  │
│  │   - secrets/（機密情報マスキング）      │  │
│  │   - mail/                            │  │
│  │  [将来実装]                           │  │
│  │   - voice/（TTS/STT）                 │  │
│  │   - image_gen/（状況画像化）           │  │
│  │   - quick_actions/（クイックアクション） │  │
│  └─────────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

理由：コアをテキストチャット一本に絞ることで、最初に動かすべきものが明確になり、設計のとっ散らかりを防げる。基本セットも「コアに必須」ではなく「hookで繋がる外付け」として扱うことで、後からの差し替え・無効化が自由になる。

---

## 3. コア設計

### 3.1 コアの責務（これだけで完結する）

- ユーザー入力を受け取る
- 履歴（直近の会話）をAPIに渡せる形式に整形する
- LLM APIを呼び出し、応答を返す
- 履歴を保存する
- フロントにストリーミング表示する

これ以外の処理（記憶検索、コスト記録、watchdog等）は一切コアに書かない。代わりに、コアの処理の要所に「hookポイント」を用意し、PluginManagerが登録済みプラグインを順に呼び出す。

### 3.2 ファイル構成（コア部分）

```
F:\LLM\hermes-work\rp-standalone\
├── .gitignore                      # Git除外設定
├── .gitattributes                  # 改行コード統一
├── .env.example                    # 環境変数テンプレート
├── README.md                       # プロジェクト概要・導入手順
├── requirements.txt                # Python依存パッケージ
├── start_server.bat                # Windows起動
├── start_server.sh                 # macOS/Linux起動
├── backend/
│   ├── main.py                     # FastAPIエントリポイント
│   ├── config.yaml
│   ├── config.default.yaml          # デフォルト設定（日本語コメント完備）
│   ├── server.log                   # RotatingFileHandler（1MB×2）
│   ├── core/
│   │   ├── api.py                  # マルチプロバイダAPI呼び出し
│   │   ├── history.py              # 履歴管理（JSONL追記専用、アトミック保存）
│   │   ├── config.py               # config.yaml読込＋${ENV}解決（ruamel.yaml＋アトミック書き込み）
│   │   ├── persona_manager.py      # ペルソナ読込・切替＋IDバリデーション
│   │   ├── session_context.py      # セッション横断コンテキスト
│   │   └── embedding.py            # 埋め込み抽象層
│   ├── plugins/
│   │   ├── base.py                 # PluginBase（+ shutdown）
│   │   ├── plugin_manager.py       # hookディスパッチ＋shutdown_all
│   │   ├── watchdog/               # 放置検知＋エスカレーション通知
│   │   ├── mail/                   # Gmail SMTP通知
│   │   ├── memory/                 # ChromaDB長期記憶（RAG）
│   │   ├── secrets/                # 機密情報プレースホルダー化
│   │   ├── session_log/            # セッションMarkdownログ
│   │   └── persona_studio/         # ペルソナ作成支援
│   └── data/
│       └── secrets_store.json
│
├── frontend/                       # マルチページ（StaticFiles配信、SSEチャット）
│   ├── index.html                  # チャット画面
│   ├── sessions.html               # セッション一覧
│   ├── session-setup.html          # 新規セッション設定
│   ├── settings.html               # 設定画面（4タブ、.setting-hint付き）
│   ├── studio.html                 # Persona Studio
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── i18n.js                 # 日英切替＋エラーコード辞書
│       ├── chat.js                 # チャットUI + SSE再接続
│       ├── sessions.js
│       ├── session-setup.js
│       ├── settings.js
│       └── studio.js
│
├── personas/                       # ペルソナ定義
│   ├── _template/                  # テンプレート（SOUL.md/SKILL.md/style.yaml）
│   └── {persona_id}/
│       ├── SOUL.md
│       ├── SKILL.md
│       └── style.yaml
│
├── sessions/                       # 会話履歴（ペルソナ別）
│   └── {persona_id}/
│       └── YYYY-MM-DD.jsonl
│
├── session-log/                    # 会話ログ（Markdown、ペルソナ別）
├── document/                       # 設計書
└── .last-response
```

コアのみで動かす場合、`backend/core/` `backend/main.py` `backend/plugins/base.py` `backend/plugins/plugin_manager.py` があれば成立する。**ペルソナ切替はコアの責務**として扱う（後述3.3.1）。

### 3.3 main.py（コア、実装済み）

```python
# 起動: python main.py [--debug] [--model MODEL_ID]
# → http://127.0.0.1:8765

# .env 自動読込（_find_dotenv → load_dotenv）
# 起動時バリデーション: .env不在 + api_key空チェック（logger.error出力、サーバーは起動継続）

@asynccontextmanager
async def lifespan(app: FastAPI):
    # watchdog配線 + configure（initialize_allより前に必須）
    # secrets設定 / session_log出力先 / memory（ChromaDB + EmbeddingProvider）設定
    await plugin_manager.initialize_all()
    init_http_client()  # max_connections=20 / keepalive=10
    try:
        yield
        # shutdown: 全プラグインのリソース解放
        await plugin_manager.shutdown_all()
    finally:
        await close_http_client()

app = FastAPI(lifespan=lifespan)
app.mount("/frontend", StaticFiles(directory=str(BASE_DIR.parent / "frontend"), html=True), name="frontend")

@app.post("/api/chat")
async def chat_sse(request: Request):
    """SSE (Server-Sent Events) によるチャットストリーミング。
    v3.1 で WebSocket から SSE に移行。"""
    touch_last_response()  # watchdog用タイムスタンプ更新

    ctx = SessionContext(persona_id=..., style=..., history=...)
    await plugin_manager.dispatch("on_session_start", ctx)

    body = await request.json()
    user_text = body.get("message", "")
    ctx.user_input = user_text

    # hook: on_user_message（watchdogリセット、secretsマスキング）
    ctx = await plugin_manager.dispatch("on_user_message", ctx)

    history.add(ctx.user_input, "")  # secrets hook でマスク済みの入力
    context_messages = history.get_context()

    # hook: on_build_context（memoryのRAG検索注入）
    context_messages = await plugin_manager.dispatch("on_build_context", context_messages, ctx)
    # hook: on_before_request（最終確認）
    context_messages = await plugin_manager.dispatch("on_before_request", context_messages, ctx)

    async def event_generator():
        try:
            async for chunk in chat_stream(context_messages, config, model_info):
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            # エラーコード化: HTTPStatusError(401)→api_key_missing 等
            yield f"data: {json.dumps({'type': 'error', 'code': error_code})}\n\n"

        history.save_turn()
        touch_last_response()

        # hook: on_response_complete
        await plugin_manager.dispatch("on_response_complete", response_text, ctx)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

コアはAPI呼び出しと履歴管理だけを知っており、具体的な処理は全てhook経由でプラグインに委譲される。
通信方式は v3.1 で WebSocket から SSE（Server-Sent Events）に移行。
エラーハンドリングは `httpx` 例外を型判定し、フロント向けにエラーコードをSSEイベントとして返す。
`core/api.py` の共有 `httpx.AsyncClient` はFastAPI lifespanで生成・終了し、OpenAI互換・Anthropic・Googleの同期／ストリーム計6経路で接続プールを再利用する。上限は20接続、KeepAliveは10接続とし、タイムアウトは従来どおり各リクエストの設定値を適用する。ストリームレスポンスは各呼出側の `async with client.stream(...)` で確実に閉じる。
フロントは `i18n.js` の `t(code)` で言語設定に応じたメッセージに変換する。

**送信・停止UI契約**:

- 入力欄右端は単一の `send-btn` とし、通常時は「送信」、SSE応答中は同じ位置・同じ要素を「停止」へ切り替える。別位置の停止ボタンは置かない
- 通常送信、再生成、ユーザー発言編集後の再送信はすべて `send()` を通り、正規化開始から応答完了までbusy guardで二重送信を拒否する
- 応答中のsend-btnは入力・機密挿入を無効化したまま停止操作だけを受け付け、停止要求の多重送信を拒否する
- 停止は `/api/chat/cancel` でサーバーへ通知し、部分応答を履歴へ保存する猶予後、上流が応答しない場合だけAbortControllerで接続を中断する
- success、API error、通信error、中断のすべてを共通finallyで通常の送信状態へ戻す
- サーバーのcancel eventはchat要求開始時に一度だけclearし、hook/context構築後にはclearしない。前処理中に届いた停止要求を失わない

### 3.3.1 PersonaManager（コア機能）

ペルソナ（人格・キャラクター設定）はオプション機能ではなく、**「どの設定ファイル（SOUL.md/SKILL.md）を読み込み、どの会話履歴を使うか」を決めるコアの根幹機能**として扱う。1キャラ固定ではなく、最初から複数ペルソナの切替運用を前提とする。

```python
# core/persona_manager.py
class PersonaManager:
    def __init__(self, config):
        self.personas_dir = Path(config.personas_dir)  # personas/
        self.active = config.default_persona
        self.default_style = config.get("style", {})  # グローバル文体設定

    def list_personas(self) -> list[dict]:
        """personas/配下をスキャンし、各SOUL.mdからペルソナ名等を抽出して一覧化"""
        return [
            {"id": d.name, "name": self._extract_name(d / "SOUL.md")}
            for d in self.personas_dir.iterdir() if d.is_dir()
        ]

    def switch(self, persona_id: str):
        if not (self.personas_dir / persona_id).exists():
            raise ValueError(f"persona not found: {persona_id}")
        self.active = persona_id

    def get_system_prompt(self) -> dict:
        """現在アクティブなペルソナのSOUL.md/SKILL.mdを読み込んでシステムプロンプト化"""
        d = self.personas_dir / self.active
        return load_prompt(d / "SOUL.md", d / "SKILL.md")
```

切替時の挙動：`switch_persona()` 呼び出しで、(1) アクティブペルソナを変更、(2) `history.reload(persona_id)` により `sessions/{persona_id}/` の履歴に切替、(3) 次回のAPI呼び出しから新しいSOUL.md/SKILL.mdがシステムプロンプトとして使われる。長期記憶（memoryプラグイン）が有効な場合は `memory_store/{persona_id}/` も連動して切り替わる想定（プラグイン側で対応）。

フロント側：チャット画面上部にペルソナ一覧（アイコンまたはドロップダウン）を表示し、クリックで `/api/persona/switch` を呼び出す。

**ペルソナ削除契約**:

- `GET /api/persona-studio/delete/{persona_id}/preview` はpersona定義、sessions（state/meta sidecarを含む）、session-log、draftの件数とactive状態だけを返し、内容は公開しない
- active personaは削除せず、先に別personaへ切り替えるよう `409 active_persona / switch_persona_required` で拒否する
- inactive personaの削除はAPI lock内で、sessions、session-log、対象current-session、draft、personaに紐づく全Memory kind、persona定義を資源単位に冪等削除する
- ファイルとChromaDBを跨ぐtransactionは作らず、`resources`、`deleted_count`、`status=ok|partial|error` を返す。部分失敗は `retry=true` と `failed_resources` を返し、同じAPIを安全に再実行できる
- フロントはpreview件数を確認dialogへ表示し、partial/error時は一覧カードを残して再試行可能にする

### 3.3.2 StyleProfile（文体設定、コア機能）

**StyleProfileはキャラクターの口調や人格を定義するものではない。** 一人称・三人称の視点選択、地の文の有無、描写方針など、物語の語り方（カメラワーク）を制御する設定である。キャラクター固有の一人称（「俺」「私」）・語尾・敬語などの言葉遣いは SOUL.md が担当する。

RPの文体は以下3軸の独立パラメータで構成し、ペルソナごとに保持する。地の文の有無は出力体裁・プロンプト構成の根幹に関わるため、PersonaManagerと同じくコアの責務として扱う（プラグインではない）。

| 軸 | 選択肢 |
|---|---|
| 語り手 | AIキャラ視点（AIが演じるキャラが語り手）／ユーザーキャラ視点（ユーザーが演じるキャラが語り手） |
| 人称表現 | 一人称（「私は」「俺は」等、語り手自身の言葉として記述）／三人称（「○○は」と外部から記述） |
| 地の文 | あり（小説形式の情景描写・心理描写を含む）／なし（セリフ・会話文のみ、いわゆる「嫁チャ」的なテンポ重視スタイル） |

3軸はすべて独立しており、設計上は2×2×2の全8パターンの組み合わせが選択可能（例: 「語り手=AIキャラ・人称=一人称・地の文なし」「語り手=ユーザーキャラ・人称=三人称・地の文あり」等）。

実際の使用頻度には偏りがあるが、その偏り方は単純に「ある軸だけがレア」ではなく、**軸同士の組み合わせに依存する**。例えば「地の文なし（嫁チャ的スタイル）」でも三人称一元視点を好むケースはあるし、「語り手=ユーザーキャラ」の場合、ユーザー自身が内心描写を書かないことが多いため、AIが代筆する三人称の方がむしろ自然に成立しやすい（一人称固定だとユーザーが内心を書かない限りAI側が書けず、書けば越権になるというジレンマが生じる）。

**UI設計（プリセット選択＋カスタム展開）**

8通りの組み合わせを毎回フルで選ばせるのではなく、よく使われる組み合わせを**プリセット**として用意し、それ以外は「カスタム」を選んだ場合のみ3トグルを展開する方式にする。プリセット自体は固定リストではなく、ペルソナ側で定義・追加できるようにする（後述）。

```
セッション開始前の選択UI:
┌─────────────────────────────────────┐
│ 文体プリセット                         │
│  ○ 小説調（地の文あり・AI視点・一人称）  ← ペルソナのデフォルト |
│  ○ 小説調・ユーザー視点（地の文あり・ユーザー視点・三人称）    │
│  ○ チャット調（地の文なし）                                │
│  ○ カスタム... → 選択時に3トグル（語り手/地の文/人称表現）を展開 │
└─────────────────────────────────────┘
```

「カスタム」を選んだ場合のみ、語り手・地の文・人称表現の3トグルが現れる。地の文トグルがOFFの間は人称表現トグルを自動でグレーアウトする（地の文がなければ人称の違いがほぼ表面化しないため）。

プリセットの実体は、その組み合わせに対応する`style.yaml`の値そのもの（例: 「小説調」＝`narration: true, viewpoint: ai_character, person: first`）であり、UIの見た目を変えているだけでデータ構造は3軸のまま変わらない。

**デフォルト値の出どころ（3層の優先順位）**: 文体設定は以下の優先順位で適用される（後勝ち）。

1. **グローバル設定**（`config.yaml` の `style` セクション）— 全ペルソナのベースライン
2. **ペルソナの `style.yaml`** — ペルソナ固有のデフォルト値
3. **セッション指定**（`style_override`）— セッション開始時にユーザーが選択した上書き

設定変更は次の新規セッションから反映される。SOUL.mdの自然言語記述から値を自動抽出するパース処理は、誤判定リスクがあるため採用しない。`style.yaml`が存在しない場合は、persona_studio（§4.6）がSOUL.mdから初回推定を行い、ユーザー確認後に`style.yaml`として書き出す。

**保持場所**: `personas/{persona_id}/style.yaml` にペルソナのデフォルトスタイルとして定義する（SOUL.md本文には文体の自然言語記述を残してもよいが、それはあくまで読み物としての説明であり、実際の挙動制御には使われない）。

```yaml
# personas/persona_a/style.yaml
style:
  viewpoint: ai_character  # 語り手 ai_character / user_character
  person: first             # 人称表現 first=一人称 / third=三人称。narration=falseの場合は実質無効
  narration: false          # 地の文 true=あり / false=なし

presets:                    # このペルソナで選べる文体プリセット一覧（ペルソナ側で追加・編集可能）
  - id: novel_ai
    label: "小説調（AI視点・一人称）"
    style: { viewpoint: ai_character, person: first, narration: true }
  - id: novel_user
    label: "小説調・ユーザー視点（三人称）"
    style: { viewpoint: user_character, person: third, narration: true }
  - id: chat
    label: "チャット調（地の文なし）"
    style: { viewpoint: ai_character, person: first, narration: false }
```

**切替タイミング**: セッション開始前のみ変更可能。一度セッションを開始した後は、そのセッション内では文体を固定し、途中切り替えは行わない（地の文の有無等が会話の途中で揺れるとRPの一貫性が崩れるため）。セッション開始前の選択は、ペルソナのデフォルト値をベースにその回だけ上書きする形にする（ペルソナ自体のデフォルト値は変更しない）。

```python
# core/persona_manager.py（StyleProfile関連の追加メソッド）
class PersonaManager:
    # ...(既存)...

    def get_default_style(self) -> dict | None:
        """アクティブペルソナのデフォルトstyle.yamlを返す。
        style.yamlが存在しない場合はNoneを返す（コアはここで止まり、persona_studioを呼び出さない。
        プラグインへの依存はコアに置かない設計のため、不在時の推定フロー呼び出しは
        呼び出し元のAPI層/フロント側の責務とする。4.7参照）。"""
        d = self.personas_dir / self.active
        style_path = d / "style.yaml"
        if not style_path.exists():
            return None
        return load_style(style_path)

    def get_presets(self) -> list[dict]:
        """style.yaml内のpresetsをUI表示用に返す。style.yaml不在時は空リスト。"""
        style = self.get_default_style()
        return style.get("presets", []) if style else []

    def start_session(self, style_override: dict = None) -> dict:
        """セッション開始時に呼ばれる。3層の優先順位でスタイルを解決:

        1. config.yaml の style（グローバル設定）
        2. ペルソナの style.yaml（ペルソナデフォルト）
        3. style_override（セッション指定、プリセット選択/カスタム）

        解決後、このセッション内でロックする（途中変更不可）。
        style.yaml 不在時は style_override が必須。
        """
        default = self.get_default_style()
        base = dict(self.default_style)          # 1. グローバル設定
        if default is not None:
            base.update(default.get("style", {})) # 2. ペルソナ style.yaml
        self._locked_style = {**base, **(style_override or {})}  # 3. セッション指定

    def get_active_style(self) -> dict:
        """現在のセッションでロックされているスタイルを返す。
        history.get_context()がシステムプロンプト構築時に参照する。"""
        return self._locked_style
```

**依存方向についての補足**: `PersonaManager`（コア）は`style.yaml`が存在しない場合に`None`を返すだけで、persona_studio（基本セットプラグイン）を一切呼び出さない。style.yaml不在時にSOUL.mdからの初回推定を行うかどうかの判断は、APIエンドポイント層（`main.py`のルートハンドラ、またはフロント側）が`get_default_style()`の戻り値を見て、`None`であれば`PersonaStudio.estimate_style_from_soul()`（4.7）を呼び出すという形にする。コアからプラグインへの直接依存は発生させない。

```python
# main.py（API層。コアとプラグインの橋渡しはここで行う）
@app.get("/api/persona/{persona_id}/style")
async def get_persona_style(persona_id: str):
    style = persona_manager.get_default_style()
    if style is None:
        # style.yaml不在時のみ、ここでプラグインを呼ぶ（コア内部では呼ばない）
        if plugin_manager.has("persona_studio"):
            estimate = plugin_manager.get("persona_studio").estimate_style_from_soul(
                read_soul_md(persona_id)
            )
            return {"status": "needs_confirmation", "estimate": estimate}
        else:
            return {"status": "needs_manual_setup"}  # persona_studio未導入時はUIで手動設定を促す
    return {"status": "ok", "style": style}
```

`get_system_prompt()`はこの`get_active_style()`の値を元に、SOUL.md/SKILL.mdの語り手・人称表現・地の文に関する指示を動的にプロンプトへ組み込む（例: 「地の文なし」が選ばれていれば、地の文を書かないよう明示的に指示する一文をシステムプロンプトに追加し、人称表現に関する指示は付与しない。「地の文あり」の場合は人称表現の値に応じて一人称/三人称いずれかで書くよう明示する）。

**フロント側**: セッション開始前（チャット入力欄が空、または新規ペルソナ起動直後）に、`style.yaml`の`presets`一覧をラジオボタンで表示し、「カスタム」選択時のみ3トグル（語り手/地の文/人称表現、地の文OFF時は人称をグレーアウト）を展開する。最初のメッセージを送信した時点で3軸すべてロックされ、UI上もグレーアウトして変更不可であることを示す。途中で変えたい場合は、新しいセッション（同ペルソナで再起動、または別セッション開始）を開く必要がある。

### 3.4 plugin_manager.py（hook機構＋プラグインライフサイクル）✅ 実装済み

```python
class PluginManager:
    HOOKS = [
        "on_session_start",
        "on_user_message",
        "on_build_context",
        "on_before_request",
        "on_response_complete",
        "on_persona_switch",
        "on_session_end",
    ]

    def __init__(self, enabled_plugins: list[str]):
        for name in enabled_plugins:
            self._load(name)
        self._sort_by_priority()

    def _sort_by_priority(self):
        """priority の昇順（小さい方が先）にソート。同値はロード順を維持。"""
        self.plugins.sort(key=lambda p: p.priority)

    async def initialize_all(self):
        """全プラグインの initialize() を呼ぶ。失敗時は即中断（再raise）。"""
        for plugin in self.plugins:
            await plugin.initialize()

    async def shutdown_all(self):
        """全プラグインの shutdown() を priority 降順（後発優先）で呼ぶ。
        各プラグインの失敗はログに残して続行（critical でも停止しない）。"""
        for plugin in reversed(self.plugins):
            try:
                await plugin.shutdown()
            except Exception:
                logger.exception("plugin shutdown failed: %s", plugin.name)

    async def dispatch(self, hook: str, data, ctx: 'SessionContext'):
        """登録済みプラグインの hook を priority 順に呼び出す。
        critical=False のプラグインは例外をログに残して続行。"""
        for plugin in self.plugins:
            if hook in plugin.hooks:
                try:
                    result = await plugin.run(hook, data, ctx)
                    if result is not None:
                        data = result
                except Exception:
                    logger.exception("%s.%s failed", plugin.name, hook)
                    if plugin.critical:
                        raise
        return data
```

```python
# plugins/base.py
class PluginBase(ABC):
    name: str
    hooks: list[str] = []
    priority: int = 100          # 小さい方が先に実行される
    critical: bool = False       # True=失敗時にチャットを中断する

    async def initialize(self):
        """プラグインの初期化。重い処理（DB接続、モデルロード等）はここで。"""
        pass

    async def shutdown(self):
        """プラグインの終了処理。タスクキャンセル、DB切断、リソース解放はここで。"""
        pass

    @abstractmethod
    async def run(self, hook: str, data, ctx: 'SessionContext'):
        """dataを処理し、必要なら書き換えて返す。書き換え不要ならNoneを返す。"""
        ...

    def get_ui_slot(self) -> dict | list[dict] | None:
        """構造化UI定義を返す。UI不要ならNone。"""
        return None

    async def handle_ui_action(self, action: str, payload: dict, ctx) -> dict:
        """payloadの業務検証は各プラグインが行う。"""
        return {"status": "error", "message": "unsupported action", "data": {}}
```

**動的プラグインUI基盤**: `get_ui_slot()` はHTML・JavaScript・CSSではなく構造化データだけを返す。スキーマversion 10は操作用の `button` / `form` と、表示専用の `separator` / `status` を扱い、配置先は `chat.input_actions`、`chat.toolbar`、`studio.actions`、`settings.plugins` の4スロットに限定する。戻り値は従来互換の単一dict、最大4件のlist、またはUIなしを示すNoneとする。複数定義ではslot重複を禁止し、各定義を最大10コンポーネント、合計最大40コンポーネントに制限する。component IDはstatus更新対象を一意にするため同一プラグインの全スロットで重複を禁止する。`status` の `text` は1〜200文字、`level` は `info` / `success` / `warning` / `error` の4値とする。定義は `PluginManager.collect_ui_definitions()` がプラグインpriority順、同一プラグイン内は宣言順に収集し、プラグイン単位のall-or-nothingで検証する。不正なプラグインは全定義を拒否してログに残し、他プラグインは継続する。

```python
{
    "slot": "chat.input_actions",
    "components": [{
        "type": "button",
        "id": "example-action",
        "label": "実行",
        "action": "run",
        "disabled": False,
    }, {
        "type": "separator",
        "id": "example-separator",
    }, {
        "type": "status",
        "id": "example-status",
        "text": "準備完了",
        "level": "success",
    }],
}
```

formは文字列fieldとboolean checkboxを次の構造で扱う。

```python
{
    "type": "form",
    "id": "search-form",
    "action": "search",
    "submit_label": "検索",
    "disabled": False,
    "fields": [
        {
            "type": "textarea",
            "id": "query",
            "label": "検索語",
            "required": True,
            "max_length": 200,
            "placeholder": "検索語を入力",
            "value": "",
        },
        {
            "type": "checkbox",
            "id": "confirm",
            "label": "確認済み",
            "required": True,
            "value": False,
        },
    ],
}
```

formは1〜10個のfieldを持つ。field `type` は `text` / `textarea` / `select` / `checkbox` / `number` / `secret` の6種とし、省略時はversion 6互換の `text` として正規化する。field IDはform内で一意、labelは1〜80文字とする。text/textareaはmax_length 1〜2000、placeholder 100文字以下、初期valueはmax_length以下とする。selectはoptionsを1〜50件に限定し、各optionは `{value, label}` だけを許可する。option valueは200文字以下でfield内一意、labelは1〜80文字とし、初期値と送信値は定義済みvalueとの完全一致を必須とする。checkboxは5属性だけを許可し、valueと送信値はboolに限定する。required checkboxはTrueを必須とし、False、0 / 1、文字列、nullを区別する。numberはnullまたは有限なint/floatだけを許可し、bool、NaN、Infinity、±1e15超過を拒否する。min/maxは任意で、初期値と送信値を範囲検証する。required numberは送信時のnullを拒否する。form actionは同一plugin内で一意とし、button actionとの衝突を禁止する。secretは `{type, id, label, required, placeholder}` のallowlistだけを受け付け、実値・初期値・password属性を定義へ含めない。実値は専用のregister APIだけが受け取り、form送信値は登録済みの `{{secret:N}}` またはoptionalのnullだけとする。form全体のdisabledだけを扱い、field単位disabled、password、file、複数選択select、checkbox groupはversion 10の対象外とする。secretの一覧・選択・reveal・実値解決も対象外とする。

form送信payloadは `{form_id, values}` の2フィールドだけを許可する。コアはform ID、全fieldの存在、未知field、field別の値型、required、text/textareaのmax_length、selectのoption一致、checkboxの厳密なbool値、numberの有限値・±1e15・min/max、secret参照の形式と登録済み確認を定義と照合し、検証済みpayloadだけをplugin handlerへ渡す。secretについてhandlerへ渡すのは参照文字列だけで、実値解決は行わない。checkboxは `type(value) is bool` でPythonのbool/intを分離し、requiredの場合はTrueだけを受理する。不正payloadは422としhandlerを呼ばない。`form_id` が存在する場合だけform検証へ入り、既存button payloadの処理は変更しない。disabled formはactionを公開しない。

APIは `GET /api/plugins/ui` で有効な定義を返し、`POST /api/plugins/{plugin_name}/actions/{action}` で定義済みかつ有効なアクションだけを実行する。POSTは同一オリジン、16KB以下のJSON objectに限定する。 Studio/Settingsではセッション開始前の操作を許容し、`SessionContext.persona_id` は空文字列になり得る。セッション必須条件は各プラグインが検証する。コアは形式・サイズ・公開アクションを検証し、各プラグインは必須キー、値型、範囲、パス等の業務検証を担当する。応答は `{status, message, data}` に固定し、messageは500文字、JSON化したdataは64KBを上限とする。buttonアクションから同一プラグインの公開済みstatusを更新する場合は、`data.ui_updates` に `{component_id, text, level}` の配列を指定する。最大10件、component_idは同一応答内で重複不可とし、対象プラグインのstatus IDだけを許可する。1件でも不正なら更新全体を拒否し、部分適用しない。フロントは対象statusの `textContent` と固定levelクラスだけを変更し、現在の画面に対象要素がなければ安全に無視する。

status更新はアクションレスポンス連動に限定する。ページ再読込時は `get_ui_slot()` の初期値へ戻る。ポーリング、SSE/WebSocket、バックグラウンド処理からの自動更新、状態永続化はversion 10の対象外とする。複数スロットで同じaction名を共有でき、いずれか1つに有効なbuttonがあればaction APIを公開する。すべてdisabledの場合は公開しない。各disabled buttonはフロントのdisabled属性により操作不能とする。

フロントの `plugin-ui.js` は受信定義をplugin名でグループ化し、定義が非連続でもplugin単位の事前検証を完了してから描画する。不正なpluginは部分描画せず、他pluginの描画は継続する。DOM APIと `textContent` だけで各コンポーネントを描画する。formはtext input / textarea / select / checkbox / number / secret参照操作、label、submit buttonをDOM APIで構築する。select optionの表示は `textContent`、値はDOMプロパティへ設定し、定義外の値を受け付けない。text/textareaにはmaxLengthとplaceholder、checkboxにはcheckedを設定し、送信時は文字列値とboolean値を型別に収集する。送信中はcontrolを一時無効化し、完了後に元の状態へ戻す。button操作とform送信には `addEventListener()` を使用する。separator/statusには固定CSSクラスだけを割り当て、プラグイン由来のclassやstyleは受け付けない。チャット、Studio、Settingsの各画面でページ内に存在するスロットだけを描画する。`plugin-ui.js` は各画面で `i18n.js` より後に読み込む。初期化とbutton操作の例外を隔離し、失敗時も各画面の既存JavaScriptへ影響させない。結果は共通フィードバック領域と `plugin-ui-result` CustomEventへ通知する。form入力値はfeedbackやログへ自動表示しない。version 10のsecret fieldはネイティブdialogから既存register APIへ実値を送信し、成功後は参照だけをformに保持する。実値をaction payload、feedback、CustomEvent、dataset、ログへ渡さず、dialogを閉じる時点で入力をクリアする。既存のsecrets `normalize` APIもテキストを受けて実値を保護できるが、Plugin UIのsecret fieldはこれを使用しない。APIキーやパスワードの汎用入力には引き続き環境変数・`.env`・secrets専用UIを使用する。

**プラグインの実行順序**: `priority` の昇順。デフォルトは100。

```
secrets     10   # 他より先にマスキング
watchdog    20   # ユーザー操作を即検知
memory      50   # コンテキスト注入
session_log 80   # 後処理
persona_studio 100 # 独立API（hook不要）
mail       100   # ユーティリティ（hook不要）
```

**失敗時の扱い**: `critical=True` のプラグイン（secrets）が例外を投げた場合、チャット処理全体を中断する。`critical=False` の場合はログ出力のみで続行。`initialize()` の失敗は常に中断。`shutdown()` の失敗はログ出力のみで続行（クリティカルでも停止しない）。

### 3.5 SessionContext（セッション横断データ）

hook 間で受け渡すコンテキスト。プラグインの追加に伴ってキーが散乱するのを防ぐため、dict ではなくクラスで管理する。プラグイン固有のデータは `extras` に格納する。

```python
# core/session_context.py
from dataclasses import dataclass, field

@dataclass
class SessionContext:
    """1セッションの会話コンテキスト。hook 間で共有される。"""

    # コア管理（読み取り専用）
    persona_id: str
    style: dict
    history: 'History'

    # プラグイン用拡張領域
    extras: dict = field(default_factory=dict)

    # ユーザー入力（on_user_message 以降で有効）
    user_input: str = ""
```

**利用イメージ**:

```python
# main.py（コア）
ctx = SessionContext(
    persona_id=persona_manager.active,
    style=persona_manager.get_active_style() or {},
    history=history,
)

# hook: on_user_message
ctx.user_input = user_text
ctx = await plugin_manager.dispatch("on_user_message", ctx)

# プラグイン側での拡張データ格納
ctx.extras["memory_hits"] = [...]  # memory プラグイン
ctx.extras["token_count"] = 1234   # cost プラグイン
```

**依存方向**: `SessionContext` はコアのデータ構造であり、プラグインからコアへの依存は発生しない（プラグインは `ctx` を受け取るだけ）。

### 3.5.1 セッションメタデータ永続化（`.meta.json`）

セッション開始時、文体（style）・記憶スコープ（memory_scope）・開始時刻を `sessions/{persona_id}/{date}_{session_id}.meta.json` に保存する。セッション再開時にこのファイルから復元し、セッション間で一貫した設定を維持する。

```python
meta = {
    "style": persona_manager.get_active_style(),
    "memory_scope": memory_scope,
    "started_at": time.time(),
}
meta_path = BASE_DIR.parent / "sessions" / persona_id / f"{session_date}_{session_id}.meta.json"
meta_path.parent.mkdir(parents=True, exist_ok=True)
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
```

### 3.6 config.yaml（コア部分、マルチプロバイダ対応）✅ 実装済み

```yaml
# プロバイダ設定（OpenRouter / OpenAI / Anthropic / Google）
providers:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}
    models:
      - nvidia/nemotron-3-ultra-550b-a55b:free
      - deepseek/deepseek-v4-pro
  openai:
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o, gpt-4o-mini]
  anthropic:
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    api_type: anthropic
    models: [claude-sonnet-4-20250514]
  google:
    base_url: https://generativelanguage.googleapis.com/v1beta
    api_key: ${GOOGLE_API_KEY}
    api_type: google
    models: [gemini-2.5-flash]

active_provider: openrouter
active_model: nvidia/nemotron-3-ultra-550b-a55b:free

api:
  max_tokens: 2000
  temperature: 0.8
  timeout: 120

session:
  max_tokens: 32000
  save_interval: 1      # 何ターンごとにファイル保存するか（1=毎ターン）

personas_dir: ../personas
default_persona: aoi-dystopia

plugins:
  enabled:
    - secrets
    - mail
    - watchdog
    - session_log
    - memory
    - persona_studio

chroma:
  path: E:/LLM/chroma
  embedding_model: intfloat/multilingual-e5-small
  embedding_cache: E:/LLM/models

watchdog:
  enabled: true
  check_interval: 60
  levels:
    - after: 300
      subject: "声かけ"
      body: "……返事がないね。..."
    - after: 900
      subject: "定規"
      body: "そろそろ15分が経とうとしている。..."
    - after: 1800
      subject: "限界"
      body: "……30分。..."

style:
  viewpoint: ai_character
  narration: true
  person: first
```

**起動時バリデーション**: `.env` 不在時とアクティブプロバイダの `api_key` が空の場合に警告をログ出力（サーバーは起動継続）。
**起動引数**: `--debug`（DEBUGログ有効）、`--model MODEL_ID`（config.yamlのモデルを上書き）。
**ポート**: 8765（`python main.py` → `uvicorn.run(app, host="127.0.0.1", port=8765)`）。
**設定リセット**: `/api/config/reset` は `config.default.yaml` をコピーする方式。コード内にデフォルト値を重複管理しない。
**ログ**: `RotatingFileHandler`（1MB×2世代）。長期運用でのログ肥大化を防止。 Windowsの標準CP932コンソールでも診断を失わないよう、loggerの固定メッセージにはCP932で表現できる区切り文字を使用する。
**フロントエンドURL**: クリーンURLで提供（`/sessions`, `/chat`, `/setup`, `/settings`, `/studio`）。`FileResponse` で `frontend/` 配下のHTMLを直接配信。CSS/JSは `/frontend/` マウントで従来通り。
**共通ナビバー**: 全ページ上部に固定ナビバー（`#top-nav`）。`[セッション] [Studio] [設定] [EN/日本語]`。現在地は `.active` でハイライト。ページ間の戻るボタンは不要。

---

## 4. 基本セットプラグイン

すべて `PluginBase` を継承し、`config.yaml` の `plugins.enabled` に名前を追加するだけで有効化される。各プラグインは独立したディレクトリに `__init__.py` + `plugin.py` を持つ。

### 4.1 watchdog（放置検知＋メール通知）✅ 実装済み

- hooks: `on_session_start`（タイマーリセット）, `on_user_message`（タイマーリセット）, `on_session_end`（監視維持、活動時刻・レベルをリセット）
- priority: 20（他プラグインより先にユーザー操作を検知）
- critical: False
- `initialize()`: バックグラウンドの asyncio 監視ループ (`_monitor_loop`) を `asyncio.create_task()` で起動
- `shutdown()`: `_stop_monitor()` でタスクを `cancel()` → `await`（`CancelledError` を suppress）
- エスカレーション: config.yaml の `watchdog.levels` で閾値（after秒）・件名・本文を定義。`enabled: false` で監視を停止可能
- `initialize()`: `enabled` が `false` の場合は監視ループを起動せずスキップ
- 動的生成: 各応答完了後に `generate_escalation_texts(config)` が会話文脈から3段階の文面をAI生成し、`set_escalation_texts()` で注入。生成失敗時は本線に影響させず `pass`
- プラグイン間連携: `set_mail_plugin()` で mail の参照を受け取り、main.py の lifespan で配線。`configure()` で config.yaml の watchdog セクションを読み込み

```yaml
# config.yaml の watchdog セクション
watchdog:
  check_interval: 60       # 監視ループ間隔（秒）
  levels:
    - after: 300           # Lv1: 5分後
      subject: "声かけ"
      body: "……返事がないね。..."
    - after: 900           # Lv2: 15分後
      subject: "定規"
      body: "そろそろ15分が経とうとしている。..."
    - after: 1800          # Lv3: 30分後
      subject: "限界"
      body: "……30分。..."
```

レベル定義は config.yaml から読み込み、動的生成された文面で上書きされる。`_current_level` はリセットされず進行度を維持。

### 4.2 mail（メール通知）✅ 実装済み

- hooks: なし（ユーティリティプラグイン。他プラグインから直接呼び出される）
- priority: 100
- critical: False
- `initialize()`: 起動時に `GMAIL_USER` / `GMAIL_APP_PASSWORD` の存在を確認し、不在時は警告をログ出力
- `send(body, subject=None) -> bool`: Gmail SMTP（smtp.gmail.com:465, SSL）経由でメールを送信。戻り値で成否を返す
- 環境変数: `GMAIL_USER`, `GMAIL_APP_PASSWORD`（必須）。`NOTIFY_FROM`, `NOTIFY_TO`, `NOTIFY_SUBJECT_TAG`（オプション、デフォルト値あり）
- 免責フッター（日英バイリンガル）を自動付与

### 4.3 memory（長期記憶／RAG）✅ 実装済み

- hooks: `on_build_context`（ユーザー入力→埋め込み→類似検索→システムプロンプトに注入）, `on_session_end`（会話履歴→LLMで事実抽出→埋め込み→ChromaDB保存）
- priority: 50（コンテキスト構築の先頭で注入）
- critical: False
- `configure()`: `EmbeddingProvider` + ChromaDB `PersistentClient` + API設定の注入。`main.py` の lifespan で配線
- `shutdown()`: ChromaDB参照・embedding provider を解放
- 非同期化: 埋め込み生成・検索とChromaDBの `get()` / `query()` / `upsert()` を `asyncio.to_thread()` でラップし、イベントループブロックを防止
- 事実抽出: 直近6000文字の会話からLLMが重要事実を抽出（`conversation[-6000:]`）
- 重複抑制: 同一ペルソナ・同一セッション内で、NFKC正規化・先頭の箇条書き／番号除去・空白統一後に完全一致する事実を保存しない。抽出結果内の重複と旧ID形式の既存文書も照合対象とする
- 保存ID: `persona_id`・`session_id`・正規化済み事実のSHA-256による決定的ID。保存は `upsert()` を使い、同じIDの再保存を安全に処理する
- 重複範囲: 意味的類似や別セッション間の統合は行わず、セッションスコープ検索の互換性を維持する
- `core/embedding.py`: `EmbeddingProvider` 抽象基底 + `SentenceTransformersProvider`（e5系モデル、`passage:`/`query:` プレフィックス、384次元、コサイン類似度）
- 依存関係: `sentence-transformers==5.6.0`、`transformers==5.12.1`、`huggingface-hub>=1.5.0,<2.0`、`chromadb==1.5.9` を `requirements.txt` で一体管理し、更新時はクリーン環境の `pip check`・import・実モデル初期化・通常起動をまとめて検証する
- コレクション: `rp_memory`（HNSW cosine）。レコードは `session_fact`（会話由来）、`persona_base`（ペルソナ確定ファイル由来の派生索引）、`legacy`（旧形式）のkindで分類する
- 通常検索: `session_fact` だけを対象とし、常時system promptへ入るSOUL/SKILLと `persona_base` を重複注入しない
- persona基本情報: `SOUL.md` / `SKILL.md` / `style.yaml` がすべて揃った保存・更新・import完了後に、追加LLM呼び出しなしで3文書を索引化する。3ファイルが正本であり、ChromaDBは再生成可能な派生索引とする
- persona索引ID: `persona_id`・`kind=persona_base`・source・NFKC/改行正規化済み内容のSHA-256。3ファイル全体の `source_hash` と短縮revisionをmetadataへ保存し、更新時は同一personaの旧 `persona_base` を置換する
- 障害境界: Memory未設定、不完全persona、embedding/ChromaDB障害でもpersona本体の保存/importは成功させ、`warning.resource=persona_base` と再構築可能フラグを返す。`POST /api/memory/personas/{persona_id}/rebuild` で確定ファイルから再構築できる
- 設定: `config.yaml` の `chroma` セクション（`path`, `embedding_model`, `embedding_cache`）

```yaml
# config.yaml
chroma:
  path: E:/LLM/chroma
  embedding_model: intfloat/multilingual-e5-small
  embedding_cache: E:/LLM/models
```

### 4.4 session_log（セッション後処理）✅ 実装済み

- hooks: `on_session_end`
- priority: 80
- 会話履歴を Markdown 形式で `session-log/{persona_id}/{date}_{session_id}.md` に保存
- セッションID単位でファイルを分離。セッション終了時に全上書き（重複追記なし）

### 4.5 cost（コスト管理）⏭ スキップ

無料モデル主体のため実装を見送り。将来的に有料モデル常用時は実装を再検討。

### 4.6 persona_studio（ペルソナ作成・編集支援）✅ 実装済み

**目的**: SOUL.md/SKILL.md/style.yamlの作成・編集をアプリ内で完結させる。

**UI構成**（オーバーレイ方式）:
- メイン: フォーム入力（全幅、中央寄せ700px）
- エディタ: 全画面オーバーレイ、SOUL.md/SKILL.mdをタブ切替で1つずつ全幅表示
- テスト会話: 全画面オーバーレイ、チャットログ＋入力欄
- ローディング: 生成中はオーバーレイ＋全ボタン無効化（連打防止）

**4タブ構成**:

| タブ | 機能 | 使用API |
|------|------|---------|
| 固定フォーム | 名前・性格・口調・背景・禁止事項・スタイルを入力 → LLMでSOUL/SKILL生成 | `create-template` |
| テキスト入力 | 自由記述・SOUL.mdテキストを貼り付け → LLMでペルソナ形式に変換 | `convert-freetext` |
| ファイル指定 | フォルダパス指定 → ファイル確認 → 3ファイル揃っていれば即登録、不足分は自動生成 | `validate-files` + `import` |
| ペルソナ一覧 | 登録済みペルソナの読込・削除。クリックで読込、ダブルクリックで削除 | `load` / `delete` |

**ファイル指定の検証**: `POST /api/persona-studio/validate-files` がSOUL.md/SKILL.md/style.yamlの有無をチェック。不足ファイルは登録時にLLMで自動生成。

**テンプレート**: `personas/_template/` にSOUL.md/SKILL.md/style.yamlのテンプレートを同梱。直接編集してファイル指定タブで登録可能。

**保存後の表示契約**: persona保存完了後もSOUL.md/SKILL.mdの編集結果と操作領域を表示したまま維持し、続けて修正・再保存・テスト会話を実行できる。保存済み一覧だけを再読込する。フォーム下書き（生成前）の読込では結果領域を表示せず、生成済み・保存済みpersonaの読込では結果領域を表示する。`beforeunload` は生成・編集中の内容がある場合に確認を行う。

**ペルソナID**: 日付ベースのデフォルト値（`persona-20260706`）を自動入力。`[a-zA-Z0-9_-]+` でバリデーション。

---

## 5. プラグイン拡張

アプリはプラグイン機構（`PluginManager` + `PluginBase`）を備えており、hook を通じて機能を拡張可能。
現在実装済みの基本セット（secrets / mail / watchdog / memory / session_log / persona_studio）に加え、
将来プラグインの構想は `backlog.md` に記載。

---

## 6. 実装状況

基本セット全6プラグイン + コア機能は **すべて実装完了**。
未着手・保留項目は `backlog.md` を参照。

---

## 7. セキュリティ設計

### 7.1 基本方針

**運用前提**: 同一PC内・同一ユーザーアカウント内での個人利用を前提とし、ディスク上のデータ（sessions/, memory_store/, personas/, cost_log.db等）は**平文で保存する**。DBやファイルを直接覗いたり編集したりできることを優先し、暗号化によるアクセス制限は行わない。マルチユーザー環境やネットワーク越しの共有利用は想定外とする。

**例外**: 上記の平文運用方針に対して、「LLM（外部API）に送信してはいけない機密情報」だけは別ルールを適用する。ローカルディスクには平文で残ってよいが、**OpenRouter API等の外部送信経路には実値を一切乗せない**ことをコードレベルで保証する。

### 7.1.1 状態履歴と会話編集の整合性

セッション状態の本体は {session_id}_state.json、副履歴は {session_id}_state_history.jsonl とする。正常完了したSTATEだけを偶数 message_count とともに記録する。編集、削除、truncate、再生成、再開では残存履歴数以下の最新状態を復元し、後続スナップショットを破棄する。STATEなし・中断・エラーは記録しない。副履歴を持たない旧セッションは再開時に既存状態を保持し、最初の状態変更操作で空状態へ安全にフォールバックする。両ファイルは原子的に置換し、UIは編集・削除・再生成の後に状態APIを再取得する。

新規セッションでは、state本体と副履歴がどちらも存在しない場合に限り、SOUL.mdの `# 開始時の状況` または `## 開始時の状況` 節を初期stateとして取り込み、message_count 0のseed snapshotを記録する。初期stateを含むSTATEはJSON整形後4,096文字を上限とし、超過したseedは自動切り詰めせず、state本体・副履歴のどちらにも保存しない。

応答から得たSTATEは既存stateへmergeし、モデルが言及しなかった項目を保持する。項目の明示的削除は `- 項目名: [解決]` だけで行い、全項目解決後の空stateも有効な会話境界として副履歴へ記録する。merge後に4,096文字を超える更新は拒否して直前stateを維持し、次回リクエストに限り状態整理指示を追加する。

STATEが2回連続で欠落した場合は、次回以降のプロンプトへ状態追跡の再確認指示を追加する。追加のLLM呼び出しは行わない。欠落回数と上限超過指示は、新規セッション開始、セッション再開、ペルソナ切替、履歴編集・削除・再生成時にリセットし、別セッションへ持ち越さない。キャンセルおよび上限超過による更新拒否はSTATE欠落回数へ加算しない。

### 7.2 機密情報マスキング機構（secrets プラグイン）✅ 実装済み（チャット／Persona Studio UI含む）

**仕組み**: プレースホルダー方式。ユーザーが「機密」とタグ付けして入力した値は、ローカルのみで保持する変数に変換され、以後のプロンプト構築・API送信・履歴保存はすべてプレースホルダー（例: `{{secret:1}}`）のまま扱われる。実値はローカルの `secrets_store`（平文JSON、一時ファイル置換でアトミック保存）に保持し、**画面表示時にのみ**アプリ側で実値展開する。

**入力方法**（2方式併存）:

1. **🔒 ボタン（標準UI）**: チャット入力欄の🔒ボタン → ラベル＋機密値入力ダイアログ → `{{secret:N}}` をカーソル位置に挿入
2. **`{{s: label: value}}` 構文（互換）**: 従来のインライン構文。送信時にサーバー側で自動的に `{{secret:N}}` に変換

**表示**:
- チャット上は `●●●●●` でマスク表示、👁ボタンで一時展開（再クリックでマスク復帰）
- ページ再読込・画面遷移で常に再マスク
- 編集時はテキストエリアにプレースホルダー文字列を表示（実値は表示しない）
- プレースホルダー削除時は確認ダイアログを表示

**API**:

| エンドポイント | メソッド | 機能 | 安全対策 |
|---------------|---------|------|---------|
| `/api/secrets/status` | GET | プラグイン有効状態確認 | `Cache-Control: no-store` |
| `/api/secrets/register` | POST | 機密値登録、プレースホルダー返却 | 同一オリジン、サイズ制限（値10KB/ラベル100字） |
| `/api/secrets/normalize` | POST | テキスト内の `{{s:}}` 構文＋実値をプレースホルダー化 | 同一オリジン、100KB上限 |
| `/api/secrets/reveal` | POST | プレースホルダー→実値復号 | 同一オリジン、レート制限（30回/60秒）、`no-store`、未登録ID共通エラー |

**Persona Studio 連携**: 外部LLM送信前に `protect_text()` で全機密値をフィルタリング。生成・修正・テスト会話のすべてのAPI呼出を保護。

**`{{s: ...}}` 互換構文**: 旧来の `{{s: label: value}}` は引き続き使用可能。バックエンドの `on_user_message` hook で自動的に `{{secret:N}}` に変換される。新規利用には🔒ボタンを推奨。

**登録済み実値の自動置換**: `protect_text()` による本文中の自動走査・置換は3文字以上の値だけを対象とする。1〜2文字の値も登録でき、🔒ボタンまたは `{{s: ...}}` 互換構文による明示的なプレースホルダー化は可能だが、通常文章への誤置換を避けるため登録後の自動走査対象にはしない。

```
[ユーザー入力（機密タグ付き）]
   │
   ▼
[secrets.register(value) → プレースホルダー発行 {{secret:N}}]
   │
   ▼
[履歴・プロンプトには {{secret:N}} のみが流れる]
   │
   ├─→ [LLM API送信] ... 実値は一切含まれない
   │
   └─→ [画面表示] ... デフォルトはマスク表示、ボタン押下で一時展開
```

**実装**: `backend/plugins/secrets/plugin.py` — `SecretsPlugin` クラス。`register()` / `protect_text()` / `reveal()` / `get_entry()` を提供。ストアは `secrets_store.json`（一時ファイル置換でアトミック保存）。Linux/macOSでは一時ファイルを作成時点から `0600` とし、既存ストアも読込前に `0600` へ補正する。権限設定に失敗した場合は機密保持を優先して初期化を失敗させる。WindowsではユーザーACLに委ね、POSIX権限操作は行わない。

**適用範囲**

1. **会話中の発言**: チャット入力欄の🔒ボタンから機密値を登録し、カーソル位置に `{{secret:N}}` を挿入。送信時、`on_user_message` hook で自動的に `{{s:}}` 構文も `{{secret:N}}` に変換され、実値は履歴・LLMに渡らない。
2. **ペルソナ設定**: Studio の下書きには `{label, placeholder}` のみ保存。実値は `secrets_store.json` で管理。SOUL/SKILL 本文にはプレースホルダー（`{{secret:1}}` 等の数値ID形式）のみが残り、保存済みペルソナの再読込時に本文中のプレースホルダーが抽出される。`secrets:` ブロックによる YAML 定義は未実装。

ペルソナ本文（地の文）では `{{secret:1}}` のように数値IDのプレースホルダーを直接記述し、実値は別途`secrets_store`に保持する。LLMに送られるシステムプロンプトにはプレースホルダーのまま渡る。

**画面表示**

- **チャット**: 機密プレースホルダーは `●●●●●` + 👁ボタンで表示。クリックで一時展開、再クリックでマスク復帰。画面遷移・再起動でマスク状態に戻る。
- **Persona Studio**: 機密項目一覧ではマスク＋👁表示。SOUL/SKILL編集テキストエリアでは `{{secret:N}}` を文字列として表示（マスクしない）。

### 7.3 フロントエンドXSS対策

ユーザー入力、API応答、ペルソナ／セッション／設定ファイル由来の値を画面へ表示する場合は、`innerHTML` のテンプレート文字列へ連結せず、DOM APIで要素を構築して `textContent`、DOMプロパティ、`dataset` に設定する。動的なIDをインラインイベント属性へ埋め込まず、`addEventListener()` のクロージャまたはイベント委譲で処理する。

F1〜F3監査で特定した優先経路に加え、フロントエンドJavaScriptの `innerHTML` は全廃し、静的HTMLのインラインイベント属性とインライン `<script>` も廃止済み。イベント処理は外部JavaScriptの `addEventListener()` に統一する。

CSPは `Content-Security-Policy-Report-Only` で検証を開始する。`default-src 'self'`、`script-src 'self'`、`style-src 'self'`、`connect-src 'self'` を基本とし、`object-src 'none'`、`base-uri 'none'`、`frame-ancestors 'none'` を指定する。違反は `POST /api/csp-report` で受信し、16KBを上限として重複を抑制してログへ記録する。URIはquery/fragmentを除去して機密情報のログ混入を防ぐ。Report-Only期間中は違反を遮断しないため、既存インラインstyleを計測・整理した後に強制CSPへ移行する。 2026-07-16のヘッドレスChrome実測では、5画面合計87件（Studio 73、setup 6、settings 4、chat 3、sessions 1）の `style-src-attr` 違反を確認し、それ以外のCSP違反は確認されなかった。 その後、全87件をCSS classへ移行し、再実測で違反0件を確認したため、正式な `Content-Security-Policy` として強制適用している。

### 7.4 ネットワーク・ポート設計

- FastAPI backendは `127.0.0.1` のみにバインドし、`0.0.0.0`では待受けない。
- ポートは `8765` 固定（`uvicorn.run(app, host="127.0.0.1", port=8765)`）。

### 7.5 ペルソナIDのパストラバーサル対策

`persona_id` は全エンドポイント入口で `validate_persona_id()`（`[a-zA-Z0-9_-]+` の正規表現マッチ）により検証される。`../` 等のパストラバーサル攻撃を防止し、`delete_persona` の `shutil.rmtree()` による任意ディレクトリ削除を防ぐ。バリデーションは以下に適用：

- `main.py`: `switch_persona`, `get_persona_style`, `save_persona`, `load_persona`, `delete_persona`
- `persona_manager.py`: `switch()`
- `persona_studio/plugin.py`: `save()`

### 7.6 ポート一覧

| サービス | ポート | 備考 |
|---|---|---|
| RPアプリ FastAPI backend | `8765` | アプリ本体のAPI/SSE |
| ComfyUI（image_genプラグイン利用時） | `8188` | ComfyUIのデフォルト値 |
| SD WebUI（image_genプラグイン利用時） | `7861` | SD WebUIデフォルト7860との衝突回避 |
| VOICEVOX Engine（voiceプラグイン利用時） | `50121` | VOICEVOXデフォルト50021との衝突回避 |

### 7.7 プラグインの信頼境界

`plugins/`配下は任意のPythonコードをロードして実行する構造のため、**自作・自己管理のプラグインのみを利用する前提**とする。第三者作成のプラグインを追加する場合は、コードレビューを行ってから`config.yaml`の`plugins.enabled`に追加する運用ルールとし、アプリ側でのサンドボックス機構（プロセス分離等）は今回のスコープでは実装しない。

### 7.8 APIキー等の認証情報

`OPENROUTER_API_KEY` / `GMAIL_APP_PASSWORD` 等は環境変数または`.env`ファイル経由で読み込み、`config.yaml`そのものには平文で書き込まない。`.env`は`.gitignore`対象とする。

**起動時バリデーション**: `.env` 不在時およびアクティブプロバイダの `api_key` が空の場合、`logger.error` で明示的なエラーメッセージを出力する（サーバーは起動継続）。
**チャット時エラー**: APIキー未設定/無効による401エラーは、バックエンドで `api_key_missing` / `api_unauthorized` のエラーコードに変換され、フロントが `i18n.js` で日英切り替え表示する。

---

## 8. プラグイン開発

プラグインは `PluginBase` を継承し、`config.yaml` の `plugins.enabled` に追加するだけで有効化される。
hook一覧、UIスロット、action API、テスト手順は [`plugin_development.md`](plugin_development.md) を参照する。コピーして利用できる無効状態の雛形は `backend/plugins/_template/` に配置する。

### 利用可能な hook（7種）

| hook | タイミング | data | 主な利用プラグイン |
|------|-----------|------|-------------------|
| `on_session_start` | セッション開始時 | `SessionContext` | watchdog（タイマーリセット） |
| `on_user_message` | ユーザー入力直後 | `SessionContext` | secrets（マスキング）、watchdog（リセット） |
| `on_build_context` | プロンプト構築直前 | `list[dict]`（messages） | memory（RAG検索注入）、secrets（リークチェック） |
| `on_before_request` | API送信直前 | `list[dict]`（messages） | （予約） |
| `on_response_complete` | AI応答完了後 | `str`（応答テキスト） | memory（記憶抽出）、session_log（ログ保存） |
| `on_persona_switch` | ペルソナ切替時 | `SessionContext` | memory（コレクション切替） |
| `on_session_end` | セッション終了時 | `SessionContext` | session_log、watchdog |

### 8.2 雛形ディレクトリ構成案

```
backend/plugins/_template/
├── __init__.py
├── my_plugin.py        # PluginBaseを継承した最小実装サンプル
└── README.md            # このプラグインの目的・hooks・設定項目を書くテンプレート
```

```python
# plugins/_template/my_plugin.py
class MyPlugin(PluginBase):
    name = "my_plugin"
    hooks = ["on_user_message"]  # 必要なhookだけ列挙

    async def run(self, hook: str, data, ctx: dict):
        if hook == "on_user_message":
            # ここに処理を書く
            return data  # 書き換えなければそのまま返す、または None
        return None

    def get_ui_slot(self) -> dict | list[dict] | None:
        return None  # UI要素が不要ならNoneのまま

    async def handle_ui_action(self, action: str, payload: dict, ctx) -> dict:
        # payloadの必須キー・型・範囲等はプラグイン側で検証する
        return {"status": "ok", "message": "完了", "data": {}}
```

この雛形をコピーし、`name`と`hooks`を書き換えて`run()`を実装するだけで新規プラグインが追加できる構成を維持する。

---

## 9. CLI版との対応表

| 機能 | CLI版（スタンドアロン設計書.md） | v3（コア/プラグイン分離版） |
|---|---|---|
| 会話ループ | main.py | コア（main.py + core/） |
| API呼び出し | api.py | コア（core/api.py） |
| 履歴管理 | history.py | コア（core/history.py） |
| watchdog | watchdog.py | 基本セットプラグイン |
| メール通知 | mail.py | 基本セットプラグイン |
| セッション後処理 | session_log.py | 基本セットプラグイン |
| 長期記憶 | なし | 基本セットプラグイン（ChromaDB + e5-small） |
| コスト管理 | なし | ⏭ スキップ（無料モデル主体） |
| ペルソナ管理 | 非対応（1キャラ固定） | コア機能（PersonaManager、切替前提） |
| 文体設定 | なし（固定） | コア機能（StyleProfile、3軸プリセット選択＋セッションロック） |
| ペルソナ作成支援 | なし（外部サービス頼み） | 基本セットプラグイン（persona_studio、テンプレート/フリーテキスト/refine/テスト会話） |
| 機密情報マスキング | なし | 基本セットプラグイン（secrets、プレースホルダー方式） |
| 音声 | なし | 将来プラグイン（新規） |
| 画像生成 | なし | 将来プラグイン（新規） |
| クイックアクション | なし | 将来プラグイン（新規） |

---

# RPスタンドアロンアプリ 詳細設計書 v3.5.0（コア／プラグイン分離版）

作成: 2026-06-30
最終更新: 2026-07-13 (v3.5.0)
ベース: `スタンドアロン設計書.md`（CLI版）/ v2からの再構成

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
                 │ HTTP / WebSocket
┌────────────────▼──────────────────────────┐
│  Python Backend (FastAPI)                  │
│  ┌─────────────────────────────────────┐  │
│  │ コア                                  │  │
│  │  - main.py（会話ループ・WebSocket）     │  │
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
│   ├── server.log                   # RotatingFileHandler（10MB×3）
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
│   │   ├── _test_dummy/            # hook発火確認用ダミー
│   │   ├── watchdog/               # 放置検知＋エスカレーション通知
│   │   ├── mail/                   # Gmail SMTP通知
│   │   ├── memory/                 # ChromaDB長期記憶（RAG）
│   │   ├── secrets/                # 機密情報プレースホルダー化
│   │   ├── session_log/            # セッションMarkdownログ
│   │   └── persona_studio/         # ペルソナ作成支援
│   └── data/
│       └── secrets_store.json
│
├── frontend/                       # SPA（StaticFiles配信、SSEチャット）
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
    yield
    # shutdown: 全プラグインのリソース解放
    await plugin_manager.shutdown_all()

app = FastAPI(lifespan=lifespan)
app.mount("/frontend", StaticFiles(directory=str(BASE_DIR.parent / "frontend"), html=True), name="frontend")

@app.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    touch_last_response()  # watchdog用タイムスタンプ更新

    ctx = SessionContext(persona_id=..., style=..., history=...)
    await plugin_manager.dispatch("on_session_start", ctx)

    while True:
        user_text = await websocket.receive_text()
        ctx.user_input = user_text

        # hook: on_user_message（watchdogリセット、secretsマスキング）
        ctx = await plugin_manager.dispatch("on_user_message", ctx)

        history.add(user_text, "")
        context_messages = history.get_context()

        # hook: on_build_context（memoryのRAG検索注入）
        context_messages = await plugin_manager.dispatch("on_build_context", context_messages, ctx)
        # hook: on_before_request（最終確認）
        context_messages = await plugin_manager.dispatch("on_before_request", context_messages, ctx)

        # ストリーミング応答
        async for chunk in chat_stream(context_messages, config, model_info):
            await websocket.send_json({"type": "chunk", "content": chunk})

        # エラーコード化: HTTPStatusError(401)→api_key_missing/api_unauthorized,
        #   TimeoutException→api_timeout, NetworkError→api_network, 他→api_unknown
        # フロント側で t(code) により日英切り替え表示

        history._save()
        touch_last_response()

        # hook: on_response_complete
        await plugin_manager.dispatch("on_response_complete", response_text, ctx)

        # watchdog用エスカレーション文面を動的生成（会話文脈からAIが生成）
        if watchdog有効:
            await generate_escalation_texts(config)
```

コアはAPI呼び出しと履歴管理だけを知っており、具体的な処理は全てhook経由でプラグインに委譲される。
エラーハンドリングは `httpx` 例外を型判定し、フロント向けにエラーコード `{"type": "error", "code": "api_key_missing"}` を返す。
フロントは `i18n.js` の `t(code)` で言語設定に応じたメッセージに変換する。

### 3.3.1 PersonaManager（コア機能）

ペルソナ（人格・キャラクター設定）はオプション機能ではなく、**「どの設定ファイル（SOUL.md/SKILL.md）を読み込み、どの会話履歴を使うか」を決めるコアの根幹機能**として扱う。1キャラ固定ではなく、最初から複数ペルソナの切替運用を前提とする。

```python
# core/persona_manager.py
class PersonaManager:
    def __init__(self, config):
        self.personas_dir = Path(config.personas_dir)  # personas/
        self.active = config.default_persona

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

**デフォルト値の出どころ（style.yamlを唯一の正とする）**: 3軸のデフォルト値は**常に `style.yaml` を正のソースとする**。SOUL.mdの自然言語記述（「一人称で統一する」等）から値を自動抽出するパース処理は、誤判定・解析失敗のリスクがあるため採用しない。`style.yaml`が存在しない場合（例: 旧形式のペルソナを読み込んだ直後、または手動でSOUL.mdだけ書いたペルソナを最初に開いたとき）に限り、SOUL.mdの記述からpersona_studio（4.7）が**初回推定**を行い、その結果をユーザーに確認させた上で`style.yaml`として書き出す。一度`style.yaml`が生成された後は、以後この自然言語パースは一切行われず、`style.yaml`の値だけが参照される。

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
        """セッション開始時に呼ばれる。style_overrideはプリセット選択時はそのプリセットのstyle値、
        カスタム選択時は3トグルの値がそのまま渡る。
        以後このセッションオブジェクト内でロックする（途中変更不可）。
        narration=falseの場合、personの値は保持はするがプロンプト構築時には実質参照しない。
        呼び出し側は事前にget_default_style()がNoneでないことを確認しておく必要がある
        （Noneの場合のフォールバック処理はAPI層が担当。4.7参照）。"""
        default = self.get_default_style() or {}
        style = {**default, **(style_override or {})}
        self._locked_style = style  # セッション中はこの値を変更不可として保持
        return style

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

    def get_ui_slot(self) -> dict | None:
        """フロントに追加するUI要素の定義を返す。なければNone。"""
        return None
```

**`get_ui_slot()` の設計意図**: バックエンドからHTML文字列を送ってフロントに挿入させるのではなく、フロント側が `/api/plugins/enabled` で有効プラグイン一覧を取得し、各プラグインのUI定義に基づいて必要なコンポーネントをマウントする方式を前提とする。

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
  save_interval: 1
  send_key: enter           # "enter" | "enter_ctrl" — 送信キー設定

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
**ログ**: `RotatingFileHandler`（10MB×3世代）。長期運用でのログ肥大化を防止。
**フロントエンドURL**: クリーンURLで提供（`/sessions`, `/chat`, `/setup`, `/settings`, `/studio`）。`FileResponse` で `frontend/` 配下のHTMLを直接配信。CSS/JSは `/frontend/` マウントで従来通り。
**共通ナビバー**: 全ページ上部に固定ナビバー（`#top-nav`）。`[セッション] [Studio] [設定] [EN/日本語]`。現在地は `.active` でハイライト。ページ間の戻るボタンは不要。

---

## 4. 基本セットプラグイン

すべて `PluginBase` を継承し、`config.yaml` の `plugins.enabled` に名前を追加するだけで有効化される。各プラグインは独立したディレクトリに `__init__.py` + `plugin.py` を持つ。

### 4.1 watchdog（放置検知＋メール通知）✅ 実装済み

- hooks: `on_session_start`（タイマーリセット）, `on_user_message`（タイマーリセット）, `on_session_end`（監視停止）
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
- 非同期化: `encode_query()` と `collection.query()` を `asyncio.to_thread()` でラップし、イベントループブロックを防止
- 事実抽出: 直近6000文字の会話からLLMが重要事実を抽出（`conversation[-6000:]`）
- `core/embedding.py`: `EmbeddingProvider` 抽象基底 + `SentenceTransformersProvider`（e5系モデル、`passage:`/`query:` プレフィックス、384次元、コサイン類似度）
- コレクション: `rp_memory`（HNSW cosine、ペルソナIDでフィルタ）
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
- 会話履歴を Markdown 形式で `session-log/{persona_id}/YYYY-MM-DD.md` に保存
- 同一ファイルが既存の場合は区切り線を入れて追記

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

**未保存防止**: `beforeunload` イベントでドラフト未保存時にブラウザ確認ダイアログを表示。保存完了後に `currentDraft` をクリア。

**ペルソナID**: 日付ベースのデフォルト値（`persona-20260706`）を自動入力。`[\w\-]+` でバリデーション。

---

## 5. 将来プラグイン（雛形のみ、設計は概要レベルに留める）

実装の優先度は低く、コア＋基本セットが安定してから着手する。

### 5.1 voice（TTS/STT）

- ローカル（VOICEVOX / faster-whisper）とクラウドAPIの切替構成、というアイデアのみ保持
- hooks案: `on_user_message`（音声入力をテキスト化）, `on_response_complete`（応答を音声合成）

### 5.2 image_gen（状況画像生成）

- ボタン一つで状況を画像化、というアイデアのみ保持
- `get_ui_slot()` でチャット画面にボタンを追加する想定

### 5.3 quick_actions（クイックアクションボタン）

- 固定＋動的提案のハイブリッド、表示モード切替（常時／折りたたみ）というアイデアのみ保持
- `get_ui_slot()` でボタン群をチャット画面に追加する想定
- 「怒る」「無視する」等のボタン定義はキャラごとのYAMLで管理する案を維持

---

## 6. 実装手順（フェーズ分け、コア優先）

| フェーズ | 内容 | 状態 |
|---|---|:--:|
| **0** | プロジェクト雛形（FastAPI疎通確認） | ✅ 完了 |
| **1** | コア: api.py / history.py / config.py / persona_manager.py + StyleProfile | ✅ 完了 |
| **2** | コア: PluginManager + base.py（hook機構＋shutdown機構） | ✅ 完了 |
| **3** | フロントSPA（5画面: sessions/session-setup/chat/settings/studio） | ✅ 完了 |
| **4** | persona_studio プラグイン | ✅ 完了 |
| **5** | コア強化: priority + SessionContext + 全7hook + lifespan shutdown | ✅ 完了 |
| 6 | 基本セット: watchdog + mail | ✅ 完了 |
| 7 | 基本セット: session_log | ✅ 完了 |
| 8 | 基本セット: memory（ChromaDB + e5-small） | ✅ 完了 |
| 9 | ~~基本セット: cost~~ | ⏭ スキップ |
| 10 | 基本セット: secrets（機密情報マスキング） | ✅ 完了 |
| 11 | 起動時バリデーション + エラーコード i18n | ✅ 完了 |
|| 12 | 将来プラグイン: quick_actions | 未着手 |
|| 13 | 将来プラグイン: voice | 未着手 |
|| 14 | 将来プラグイン: image_gen | 未着手 |
|| — | **コア＋基本セット** | **✅ 全完了** |
|| **15** | セッション管理拡張（ID・再開・履歴編集・truncate） | ✅ 完了 |
|| **16** | watchdog デフォルト OFF + 無効時エスカレーション抑制 | ✅ 完了 |
|| **17** | フロント: チャット履歴表示・編集・削除・再生成 | ✅ 完了 |
|| **18** | フロント: セッション一覧「続きから」機能 | ✅ 完了 |

---

## 7. セキュリティ設計

### 7.1 基本方針

**運用前提**: 同一PC内・同一ユーザーアカウント内での個人利用を前提とし、ディスク上のデータ（sessions/, memory_store/, personas/, cost_log.db等）は**平文で保存する**。DBやファイルを直接覗いたり編集したりできることを優先し、暗号化によるアクセス制限は行わない。マルチユーザー環境やネットワーク越しの共有利用は想定外とする。

**例外**: 上記の平文運用方針に対して、「LLM（外部API）に送信してはいけない機密情報」だけは別ルールを適用する。ローカルディスクには平文で残ってよいが、**OpenRouter API等の外部送信経路には実値を一切乗せない**ことをコードレベルで保証する。

### 7.2 機密情報マスキング機構（secrets プラグイン）

**仕組み**: プレースホルダー方式。ユーザーが「機密」とタグ付けして入力した値は、ローカルのみで保持する変数に変換され、以後のプロンプト構築・API送信・履歴保存はすべてプレースホルダー（例: `{{secret:1}}`）のまま扱われる。実値はローカルの`secrets_store`（暗号化なしのJSON/SQLite、9.1の方針通り平文）に保持し、**画面表示時にのみ**アプリ側で実値展開する。

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

```python
# plugins/secrets/secrets_manager.py
class SecretsManager(PluginBase):
    name = "secrets"
    hooks = ["on_user_message", "on_build_context"]

    def __init__(self):
        self.store = load_secrets_store()  # ローカル平文ストア（同一PC前提）

    def register(self, value: str, label: str = None) -> str:
        """機密値を登録し、プレースホルダーを発行"""
        token = f"{{{{secret:{generate_id()}}}}}"
        self.store[token] = {"value": value, "label": label}
        return token

    def run(self, hook: str, data, ctx: dict):
        if hook == "on_user_message":
            # 入力欄で機密タグ付けされた部分をプレースホルダーに置換してから履歴に渡す
            return self._mask_tagged_input(data)
        if hook == "on_build_context":
            # コンテキスト構築後も常にプレースホルダーのままであることを再確認（誤って実値混入していないか検証）
            return self._assert_no_leak(data)

    def reveal(self, token: str) -> str:
        """表示用に実値を一時展開。フロントの『目』ボタンから呼ばれる"""
        return self.store.get(token, {}).get("value", "[unknown]")
```

**適用範囲**

1. **会話中の発言**: チャット入力欄に「🔒 機密として入力」のトグル付きフィールドを用意する。ONで送信した内容は自動的に`secrets.register()`を通り、プレースホルダー化されてから履歴・LLMに渡る。
2. **ペルソナ設定自体**: SOUL.md/SKILL.mdのテンプレート（4.7 persona_studio）に「機密項目」セクションを設け、`secrets:` ブロックとして定義できるようにする。例えばユーザーの本名・勤務先名等をペルソナ設定の一部として使いたい場合、ここに登録すればプレースホルダー化された状態でSOUL.mdに埋め込まれる。

```yaml
# personas/persona_a/SOUL.md 内に埋め込む機密項目定義例
secrets:
  - id: user_real_name
    label: "ユーザーの本名"
    placeholder: "{{secret:user_real_name}}"
  - id: workplace
    label: "勤務先"
    placeholder: "{{secret:workplace}}"
```

ペルソナ本文（地の文）では `{{secret:user_real_name}}` のようにプレースホルダーを直接記述し、実値は別途`secrets_store`に保持する。LLMに送られるシステムプロンプトにはプレースホルダーのまま渡る（あるいは汎用的な代替表現に置換した上で渡る運用も選べるようにする想定。詳細は実装フェーズで調整）。

**画面表示**

チャットログ・ペルソナ編集画面ともに、機密プレースホルダーはデフォルトで `●●●●●` のようなマスク表示にする。各マスク表示の隣に「👁」ボタンを配置し、押下している間（またはトグルで明示的にOFFにするまで）のみ実値を表示する。マスク解除状態はその場限りで、画面遷移や再起動でマスク状態に戻る。

### 7.3 ネットワーク・ポート設計

- FastAPI backendは `127.0.0.1` のみにバインドし、`0.0.0.0`では待受けない。
- ポートは `8765` 固定（`uvicorn.run(app, host="127.0.0.1", port=8765)`）。

### 7.4 ペルソナIDのパストラバーサル対策

`persona_id` は全エンドポイント入口で `validate_persona_id()`（`[\w\-]+` の正規表現マッチ）により検証される。`../` 等のパストラバーサル攻撃を防止し、`delete_persona` の `shutil.rmtree()` による任意ディレクトリ削除を防ぐ。バリデーションは以下に適用：

- `main.py`: `switch_persona`, `get_persona_style`, `save_persona`, `load_persona`, `delete_persona`
- `persona_manager.py`: `switch()`
- `persona_studio/plugin.py`: `save()`

### 7.5 ポート一覧

| サービス | ポート | 備考 |
|---|---|---|
| RPアプリ FastAPI backend | `8765` | アプリ本体のAPI/WebSocket |
| ComfyUI（image_genプラグイン利用時） | `8188` | ComfyUIのデフォルト値 |
| SD WebUI（image_genプラグイン利用時） | `7861` | SD WebUIデフォルト7860との衝突回避 |
| VOICEVOX Engine（voiceプラグイン利用時） | `50121` | VOICEVOXデフォルト50021との衝突回避 |

### 7.6 プラグインの信頼境界

`plugins/`配下は任意のPythonコードをロードして実行する構造のため、**自作・自己管理のプラグインのみを利用する前提**とする。第三者作成のプラグインを追加する場合は、コードレビューを行ってから`config.yaml`の`plugins.enabled`に追加する運用ルールとし、アプリ側でのサンドボックス機構（プロセス分離等）は今回のスコープでは実装しない。

### 7.7 APIキー等の認証情報

`OPENROUTER_API_KEY` / `GMAIL_APP_PASSWORD` 等は環境変数または`.env`ファイル経由で読み込み、`config.yaml`そのものには平文で書き込まない。`.env`は`.gitignore`対象とする。

**起動時バリデーション**: `.env` 不在時およびアクティブプロバイダの `api_key` が空の場合、`logger.error` で明示的なエラーメッセージを出力する（サーバーは起動継続）。
**チャット時エラー**: APIキー未設定/無効による401エラーは、バックエンドで `api_key_missing` / `api_unauthorized` のエラーコードに変換され、フロントが `i18n.js` で日英切り替え表示する。

---

## 8. プラグイン開発者向け文書

外部に頼らず自分でプラグインを書き足せるよう、最低限の開発ガイドを用意する。コアの実装が固まった段階（フェーズ4以降）で `docs/plugin_development.md` として整備する想定。

### 8.1 文書に含める内容

- **PluginBaseの実装方法**: `name` / `hooks` の定義、`run()`の戻り値の扱い（dataを書き換えて返す／Noneで無変更）
- **利用可能なhook一覧と発火タイミング**: `on_user_message` / `on_build_context` / `on_response_complete` / `on_session_end` それぞれが「会話ループのどの瞬間に」「どんなdata/ctxを受け取るか」を明記
- **UIスロットの追加方法**: `get_ui_slot()` の戻り値仕様（ボタン1個追加したい場合、パネルを追加したい場合のサンプル）
- **既存プラグインのコード例**: 基本セット（watchdog, memory, cost等）を「読めばわかるサンプル実装」として位置づけ、新規プラグイン作成時の雛形として案内
- **config.yamlへの登録方法**: `plugins.enabled` への追加、プラグイン固有設定の置き場所のルール
- **secretsプラグインとの連携方法**: 機密値を扱うプラグインを作る場合、`SecretsManager.register()`/`reveal()`をどう呼ぶか（7.2参照）
- **ポート/外部プロセス利用時の注意**: image_gen/voiceのようにローカル外部サーバーと通信するプラグインを作る場合のポート割当ルール（7.3の表に追記する形で管理）
- **テンプレート雛形ファイル**: `plugins/_template/` に最小限のひな形（`base.py`を継承した空実装）を同梱し、コピーして使えるようにする

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

    def run(self, hook: str, data, ctx: dict):
        if hook == "on_user_message":
            # ここに処理を書く
            return data  # 書き換えなければそのまま返す、または None
        return None

    def get_ui_slot(self) -> dict | None:
        return None  # UI要素が不要ならNoneのまま
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

## 10. 今後の検討事項（未確定・要相談）

### 10.1 アーキテクチャ

- プラグイン間の依存関係（memory が session_log の出力に依存する等）の順序制御
- フロント側のプラグインUI動的追加の具体的な実装方式（現在は全画面を静的に実装）
- **モバイル対応**: `host="0.0.0.0"` + `--host` 起動オプションで同一LAN内のスマホ・タブレットからアクセス可能にする。認証機構は別途検討（現状は同一PC内利用前提のため、LAN公開時は自己責任）。Studio画面は分割パネル（左フォーム＋右エディタ）のため、スマホでは縦積みレイアウトへの変更が必要。

### 10.2 将来修正候補（優先度低・現状問題なし）

以下はコードレビューで指摘されたが、現状では実害がなく緊急度が低いため保留している項目。

| # | 内容 | 条件 |
|---|------|------|
| 1 | `History.save_turn()` が `_messages[-2:]`（最後の2件=user+assistant）を前提としている。将来 tool/plugin 等のメッセージ種別が追加された場合、明示的に「最後の1ターン」を取得する方式に変更 | メッセージ種別追加時 |
| 2 | `History._load_latest()` で破損JSONL読み込み時にroleの並び（user→assistantの交互）検証がない。クラッシュ等で不完全な行が混入した場合、次回起動時に不正なコンテキストが構築される可能性 | ファイル破損の報告時 |
| 3 | memory プラグインの重複記憶抑制（同一factの重複保存防止）。現在は同一事実が複数回抽出されるとChromaDBに重複保存される | 長期間運用で検索精度低下が確認された場合 |
| 4 | HTTPクライアント（`httpx.AsyncClient`）のインスタンス再利用。現在は毎リクエストで新規生成しており、KeepAlive/TCP再利用が効かない | プラグイン数増加・高頻度API呼び出し時 |
| 5 | 設定変更API（`/api/config/*`）の値バリデーション（型・範囲チェック）。現在はキーの許可リストのみで、不正な値がconfig.yamlに書き込まれる可能性 | フロント以外からのAPI直叩き運用時 |
|| 6 | `session-setup.js` の `persona_id` 属性埋め込み時のエスケープ。`p.id` に二重引用符が混入すると属性脱出が理論上可能（セルフXSS） | 悪意あるpersona_idの混入時（ローカルアプリのためリスク極小） |

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
- 履歴メッセージをWebSocket接続時に `type: "history"` で全送信
- 各メッセージに `[編集] [再生成(ユーザーのみ)] [削除]` ボタン
- ユーザー発言編集 → truncate + 編集後テキストを再送信（`resend` フラグで重複追加防止）
- AI応答編集 → `[編集済]` ラベル付与、正式履歴として扱う
- ダブルクリックでなくボタンクリックによる編集起動、テキストエリアは 70ch 幅

### 11.3 応答中画面移動の保護

WebSocket切断（応答ストリーミング中に画面移動）を `WebSocketDisconnect` で捕捉し、受信済みテキストに `[中断]` を付与してJSONLに保存。ユーザー発言は失われない。

### 11.4 watchdog デフォルト OFF

`config.yaml` の `watchdog.enabled` をデフォルト `false` に変更。無効時は監視ループ・エスカレーション文面生成の両方をスキップ。

### 11.5 チャット入力

config.yaml の `session.send_key` で送信キーを切替可能。

| send_key | Enter | Ctrl+Enter | Shift+Enter |
|---|---|---|---|
| `"enter"`（デフォルト） | 送信 | 改行 | 改行 |
| `"enter_ctrl"` | 送信 | 送信 | 改行 |

chat.js は `DOMContentLoaded` で `/api/config/full` から `session.send_key` を読み込み、グローバル変数 `sendKeyMode` に保存。`msg-input` の `keydown` ハンドラ内で条件分岐:
```js
if (e.key === "Enter" && !e.shiftKey) {
  const shouldSend = (sendKeyMode === "enter_ctrl") || !e.ctrlKey;
  if (shouldSend) { e.preventDefault(); send(); }
}
```
設定画面: 詳細タブ → 「送信キー」プルダウン → 「適用」。i18n キー: `labelSendKey`, `optSendKeyEnter`, `optSendKeyEnterCtrl`, `hintSendKey`。

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

### 15.9 asyncio.Lock（準備）

複数ブラウザタブからの同時リクエストによるデータ競合を防止するため、`_api_lock = asyncio.Lock()` を追加。各エンドポイントへの適用は後続タスク。

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

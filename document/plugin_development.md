# プラグイン開発ガイド

> 現行UIスキーマ: version 9<br>
> アーキテクチャとセキュリティの正本は `RPスタンドアロンアプリ_設計書.md`。この文書は実装手順とコード例を扱う。

## 1. 信頼境界

`backend/plugins/` のプラグインはアプリ本体と同じプロセスで任意のPythonコードを実行する。プロセス分離やsandboxはないため、自作またはコードレビュー済みのプラグインだけを有効化する。

必須ルール:

- 外部入力、設定、ファイルパス、API応答を信用しない
- パスは許可されたルート内か検証する
- APIキーは環境変数または `.env` から読み、設定やログへ書かない
- 動的UIのtext formへパスワードやAPIキーを入力しない。機密値はsecrets専用UIを使う
- UI定義へHTML、JavaScript、CSS、イベント属性を含めない
- 外部通信にはtimeoutを設定する
- 重い同期I/Oは `asyncio.to_thread()` 等へ逃がす
- task、HTTP client、DB接続は `shutdown()` で解放する

## 2. 作成手順

コピー可能な雛形は `backend/plugins/_template/` にある。

```text
backend/plugins/<plugin_name>/
├── __init__.py
├── plugin.py
└── README.md
```

1. `_template` を新しい名前へコピーする
2. クラス名と `name` を変更する
3. 不要なhookとUIを削除する
4. テストを追加する
5. `backend/config.yaml` の `plugins.enabled` にディレクトリ名を追加する
6. 起動・操作・終了を確認する

初期設定の正本は `backend/config.default.yaml`。`_template` はどちらの設定でも有効化しない。

## 3. PluginBase

```python
from plugins.base import PluginBase


class MyPlugin(PluginBase):
    name = "my_plugin"
    hooks = []
    priority = 100
    critical = False

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def run(self, hook: str, data, ctx):
        return data

    def get_ui_slot(self) -> dict | list[dict] | None:
        return None

    async def handle_ui_action(self, action: str, payload: dict, ctx) -> dict:
        return {"status": "error", "message": "unsupported action", "data": {}}
```

1つの `plugin.py` には `PluginBase` の実装クラスを1つだけ置く。`name` は `[a-zA-Z0-9_-]{1,64}` とし、ディレクトリ名と揃える。

## 4. ライフサイクル

- `initialize()`: DB、model、client、background taskの準備
- `shutdown()`: task cancel、client close、DB close
- initializeはpriority昇順
- shutdownはpriority降順
- initialize失敗は起動を中断
- shutdown失敗はログに残して他pluginの終了を継続

初期化前提のresourceを `__init__()` で重く生成しない。

## 5. hook

| hook | data | ctx引数 | 用途 |
|---|---|---|---|
| `on_session_start` | `SessionContext` | 現状は`None` | セッション初期化 |
| `on_user_message` | `SessionContext` | 現状は`None` | 入力変換、操作検知 |
| `on_build_context` | `list[dict]` | `SessionContext` | RAG、context追加 |
| `on_before_request` | `list[dict]` | `SessionContext` | 外部送信直前検査 |
| `on_response_complete` | `str` | `SessionContext` | 応答後処理 |
| `on_persona_switch` | `None` | `SessionContext` | persona別resource切替 |
| `on_session_end` | `None` | `SessionContext` | 保存、cleanup |

`run()` はデータを書き換える場合に新しいdataを返し、変更しない場合も受け取ったdataを返すのが安全。`None` を返すとPluginManagerは元のdataを維持する。

`on_session_start` と `on_user_message` ではSessionContextがdataとして渡される点に注意する。

## 6. SessionContext

主な属性:

- `persona_id`: active persona ID
- `style`: セッション固定style
- `history`: History object
- `memory_scope`: `session` または `persona`
- `extras`: plugin固有の共有領域
- `user_input`: on_user_message以降の入力

`persona_id`、`style`、`history` はコア管理として扱う。plugin固有値は衝突を避けるため `ctx.extras["my_plugin"]` のように名前空間を切る。

## 7. priorityとcritical

priorityは小さい順に実行され、同値では設定上のロード順を維持する。

| plugin | priority |
|---|---:|
| secrets | 10 |
| watchdog | 20 |
| memory | 50 |
| session_log | 80 |
| その他 | 100 |

特別な順序要件がなければ100を使う。

`critical=True` は失敗後の処理継続が危険な場合だけ使う。例は外部送信前の機密値保護。通常の補助機能はfalseにする。

## 8. 動的UI version 9

`get_ui_slot()` は単一dict、最大4件のlist、またはNoneを返す。

対応slot:

- `chat.input_actions`
- `chat.toolbar`
- `studio.actions`
- `settings.plugins`

制限:

- 同一pluginでslot重複禁止
- 1定義1〜10 components
- plugin合計最大40 components
- component IDは全slotで一意
- 1件でも不正ならpluginの全UIを拒否
- plugin priority順、同一pluginは宣言順

対応componentはbutton、separator、status、form。未知フィールドは拒否される。

## 9. button、separator、status

```python
{
    "slot": "chat.toolbar",
    "components": [
        {
            "type": "button",
            "id": "refresh-button",
            "label": "Refresh",
            "action": "refresh",
            "disabled": False,
        },
        {"type": "separator", "id": "separator"},
        {
            "type": "status",
            "id": "state",
            "text": "Ready",
            "level": "info",
        },
    ],
}
```

status levelは `info` / `success` / `warning` / `error`。button同士はactionを共有でき、1つ以上が有効ならactionを公開する。

## 10. 文字列form

```python
{
    "type": "form",
    "id": "settings-form",
    "action": "save_settings",
    "submit_label": "Save",
    "disabled": False,
    "fields": [
        {
            # type省略時はtextとして扱う（version 6互換）
            "id": "display_name",
            "label": "Display name",
            "required": True,
            "max_length": 80,
            "placeholder": "Plugin name",
            "value": "",
        },
        {
            "type": "textarea",
            "id": "notes",
            "label": "Notes",
            "required": False,
            "max_length": 2000,
            "placeholder": "Optional notes",
            "value": "",
        },
        {
            "type": "select",
            "id": "mode",
            "label": "Mode",
            "required": True,
            "options": [
                {"value": "safe", "label": "Safe"},
                {"value": "fast", "label": "Fast"},
            ],
            "value": "safe",
        },        {
            "type": "checkbox",
            "id": "enabled",
            "label": "Enable feature",
            "required": False,
            "value": False,
        },
    ],
}
```

- fieldsは1〜10件、field IDはform内で一意
- `type` は `text` / `textarea` / `select` / `checkbox` / `number`。省略時は `text`
- text/textareaのmax_lengthは1〜2000、placeholderは100文字以下
- selectのoptionsは1〜50件、各optionは `{value, label}` のみ
- option valueは200文字以下かつfield内で一意、labelは1〜80文字
- selectの初期値と送信値は定義済みoption valueに限定
- checkboxのvalueと送信値はboolのみ。requiredの場合はTrue必須
- numberは有限なint/floatまたはnull。±1e15以内でmin/maxを検証
- form actionはplugin内で一意、button actionとの衝突禁止
- password、file、複数選択select、checkbox group、number stepは未対応

送信payloadは固定の `{form_id, values}` 形式:

```python
{
    "form_id": "settings-form",
    "values": {"display_name": "Example", "enabled": False},
}
```

コアは構造、field集合、型、required、max_length、selectのoption一致、checkboxのbool値、およびnumberの有限値・min/maxを検証する。pluginは値の意味、許可範囲、identifier、path等を追加検証する。

## 11. UI action

応答形式:

```python
return {
    "status": "ok",
    "message": "Saved",
    "data": {},
}
```

- status: `ok` または `error`
- message: 最大500文字
- data: JSON化可能なobject、最大64KB
- set、NaN、任意class instance等は不可
- 未定義actionは安全なerrorを返す
- 例外を利用者向けmessageへそのまま含めない

## 12. status動的更新

```python
return {
    "status": "ok",
    "message": "Updated",
    "data": {
        "ui_updates": [{
            "component_id": "state",
            "text": "Ready",
            "level": "success",
        }],
    },
}
```

最大10件。同一pluginが公開したstatus IDだけを更新できる。1件でも不正なら更新全体を拒否する。HTML、任意class、style、attributeは指定できない。

## 13. エラー処理

- `critical=False` のhook例外はログ後に他pluginへ進む
- `critical=True` のhook例外は処理全体を中断
- UI定義取得失敗はそのpluginだけを隔離
- 不正UI定義はplugin単位で全拒否
- 未公開actionは404相当
- 不正payloadは422相当
- plugin action例外は固定error応答へ変換

利用者向けmessageと詳細ログを分け、token、password、本文等を詳細ログへ含めない。

## 14. テスト

最低限確認する項目:

- plugin load
- initialize / shutdown
- hook正常系と例外
- priority / critical
- UI定義の正常系と拒否系
- action未定義・disabled
- form payload正常・欠落・未知field・型・長さ
- status更新
- resource解放

```powershell
python -m py_compile backend/plugins/my_plugin/plugin.py
python -m unittest tests.test_regressions
node --check frontend/js/plugin-ui.js
git diff --check
```

## 15. 完了チェックリスト

- [ ] 自作またはレビュー済みコードだけを使用
- [ ] `name` とディレクトリ名が一致
- [ ] 必要なhookだけを宣言
- [ ] priorityの理由が明確
- [ ] criticalは必要最小限
- [ ] 外部入力を業務検証
- [ ] timeoutを設定
- [ ] 機密値をログへ出さない
- [ ] shutdownでresourceを解放
- [ ] UI ID・slot・actionの一意性を確認
- [ ] formへ機密値を入力させない
- [ ] 正常系・拒否系テストを追加
- [ ] `backend/config.yaml` で有効化
- [ ] 通常起動と正常終了を確認

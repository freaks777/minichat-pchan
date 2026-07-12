# main.py スリム化・重複排除 提案書

対象: `backend/main.py`（現状1644行）ほか
目的: 構成の整理と処理の重複排除。機能変更・挙動変更は行わない（リファクタリングのみ）。

前提: Sonnet/Gemini双方のセキュリティレビュー指摘（session_id/persona_idバリデーション、
escapeHtml、_save_fullアトミック化、os import漏れ）は対応済み。今回はその上での構造改善提案。

---

## 優先度1: config.yaml 読み書きヘルパーの共通化

### 現状の問題
以下のエンドポイントで、同一の「YAML読込→更新→書込」パターンが**5箇所**重複している。

- `set_provider`（447行〜）
- `set_api_params`（495行〜）
- `set_watchdog`（519行〜）
- `set_session_config`（544行〜）
- `set_style`（592行〜）

各所で以下のコードがほぼそのまま繰り返される:

```python
import yaml
config_path = CONFIG_PATH
with open(config_path, "r", encoding="utf-8") as f:
    raw = yaml.safe_load(f)
# ...個別の更新処理...
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(raw, f, allow_unicode=True, sort_keys=False)
```

`import yaml` が関数内で毎回書かれている点も含め、書き忘れ・更新漏れの温床になりやすい
（history.pyのimport os漏れと同種のミスが起きやすい構造）。

### 提案

`core/config.py` に以下のヘルパーを追加:

```python
from typing import Callable

def update_config_yaml(config_path: Path, mutator: Callable[[dict], None]) -> dict:
    """config.yaml を読み込み、mutatorで書き換えてから保存する。

    Args:
        config_path: 対象のconfig.yamlパス
        mutator: raw dict を直接書き換える関数（戻り値不要）

    Returns:
        更新後のraw dict
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    mutator(raw)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, sort_keys=False)
    return raw
```

呼び出し側は例えば `set_style` なら:

```python
@app.post("/api/config/style")
async def set_style(data: dict):
    style = data.get("style", {})
    allowed = {"viewpoint", "narration", "person"}
    update = {k: (bool(style[k]) if k == "narration" else style[k])
              for k in allowed if k in style}

    def mutator(raw: dict):
        raw.setdefault("style", {}).update(update)

    update_config_yaml(CONFIG_PATH, mutator)

    config.setdefault("style", {}).update(update)
    logger.info("global style updated: %s", update)
    return {"status": "ok", "style": config["style"]}
```

他4箇所も同様に `mutator` 部分だけ差し替えれば良い。

**効果**: 5箇所 × 約8行 → ヘルパー1つ（10行程度）+ 各呼び出し3〜4行に圧縮。
ファイル冒頭で `import yaml` を一度書けば関数内 `import yaml` も全廃できる。

---

## 優先度2: `_auto_resume_session` と `resume_session` の統合

### 現状の問題
両者は「persona_id/session_idを検証し、ペルソナと履歴を切り替える」という
ほぼ同じ処理を別々に実装している（935〜1105行、合計約170行）。

過去の経緯として、`resume_session` 側にのみ持っていた `session_id` の
フォーマット検証（`\d{4}-\d{2}-\d{2}_\d{8}`）が `_auto_resume_session` 側に
存在しなかったため、パストラバーサルの脆弱性を生んだ（対応済み）。
**ロジックが2箇所に分かれていること自体が再発リスク**なので、根本対応として統合を推奨。

### 提案

共通の内部関数 `_resolve_session(persona_id, session_id, *, require_session_format=False)` を作り、
両エンドポイントはこれを呼ぶだけにする。

```python
async def _resolve_session(
    persona_id: str,
    session_id: str = "",
    *,
    notify_end: bool = True,
) -> str | None:
    """persona_id/session_id を検証し、ペルソナと履歴を切り替える共通処理。

    Returns:
        エラーメッセージ（文字列）。成功時は None。
    """
    if not persona_id:
        return None

    try:
        validate_persona_id(persona_id)
    except ValueError as e:
        return str(e)

    if session_id:
        import re
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{8}", session_id):
            return f"invalid session_id format: {session_id}"

    if persona_id == persona_manager.active:
        return None  # 既に同一ペルソナなら何もしない

    # ここから下は現行の _auto_resume_session 本体とほぼ同じ
    ...
```

- `_auto_resume_session` からの呼び出し（`/api/chat` 等）は `notify_end=True` のまま。
- `resume_session`（`/api/session/resume`）は明示的なエンドポイントなので、
  今まで通り個別のログメッセージ・`resumed_from` フィールドなどはルート側で追加する。

**効果**: 170行 → 共通ロジック約100行 + 各呼び出し側10〜20行。
バリデーションロジックが1箇所になるため、今後同種のセッション関連エンドポイントを
追加してもバリデーション漏れが起きなくなる。

---

## 優先度3: `main.py` の `APIRouter` 分割

### 現状
1644行の単一ファイルに、起動処理・設定API・ペルソナAPI・セッションAPI・
persona_studio API・チャットSSE・ページルーティングが同居している。

コメント区切り（`# ── ... ──`）で見ると、すでに論理的な塊は明確:

| 範囲（現在の行数） | 内容 | 移動先 |
|---|---|---|
| 412〜621行（約210行） | `/api/config/*` | `routers/config.py` |
| 622〜804行（約180行） | `/api/persona/*`, `/api/sessions/list`, `delete_session` | `routers/persona.py` |
| 805〜1198行（約395行） | `/api/session/*` + セッション解決処理 | `routers/session.py` |
| 1199〜1411行（約210行） | `/api/persona-studio/*` | `routers/persona_studio.py` |
| 1412〜1610行（約200行） | `/api/chat`（SSE） | `routers/chat.py` |
| 1611〜1639行（約30行） | ページルーティング | `routers/pages.py` |

残る `main.py` は起動処理（`lifespan`、`.env`探索、ロギング設定、プラグイン初期化、
`app.include_router()` の呼び出し）のみとなり、**150行程度**まで縮む見込み。

### 共有状態の扱い

`config` / `persona_manager` / `history` / `plugin_manager` / `logger` は現状モジュール
グローバル変数。routerに分割する際は、以下いずれかの方式を推奨:

**方式A（推奨・変更が小さい）**: `core/state.py` を新設し、上記のグローバル変数を
そこに集約。各routerは `from core.state import config, persona_manager, history, ...`
のようにimportする。`main.py` 側の初期化コード（`lifespan`内など）も `core.state` の
変数を書き換える形にする。

**方式B（FastAPI標準に寄せる）**: `app.state` に載せ、各routerのエンドポイントで
`request: Request` を引数に取り `request.app.state.xxx` でアクセスする。より
「正しい」やり方だが、全エンドポイントの引数を書き換える必要があり変更量が多い。

→ 今回は変更量を抑えたいので **方式A** を推奨。

### 進め方
1. まず優先度1・2を先に完了させ、動作確認する
2. その後、router分割は1ファイルずつ段階的に移す（例: 最初に `routers/config.py` だけ切り出し、動作確認してから次へ）
3. 移動時は `import` 文と `router = APIRouter()` の付け替えのみで、ロジック自体は変更しない

---

## 優先度: 検討のみ（今回は対象外）

- `plugins/persona_studio/plugin.py`（583行）: 他ファイルに比べ突出して大きい。
  `estimate_style` / `create_via_template` / `extract_fields` / `convert_freetext` /
  `refine` / `test_chat` の各LLM呼び出しでプロンプト構築パターンが重複している
  可能性がある。中身を精査していないため、優先度1〜3が落ち着いた後に別途調査を推奨。

---

## まとめ

| 優先度 | 内容 | 効果 | リスク |
|---|---|---|---|
| 1 | config.yaml読み書きヘルパー共通化 | 5箇所×8行 → 大幅圧縮、書き忘れ防止 | 低（影響範囲が狭い） |
| 2 | `_auto_resume_session`/`resume_session`統合 | 170行→120行程度、バリデーション漏れの構造的防止 | 中（両エンドポイントの挙動を丁寧に確認要） |
| 3 | `main.py`のAPIRouter分割 | 1644行→150行+router各200行程度 | 中〜高（共有状態の扱いを要検討、段階的に実施） |

1→2→3の順に進め、各段階でruff・py_compile・実際のエンドポイント疎通確認を挟むことを推奨する。

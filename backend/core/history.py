"""履歴管理。JSON Lines 形式で保存・読込。トークン詰め対応。"""

import json
import os
import time
from pathlib import Path
from typing import Optional


class History:
    """会話履歴を管理する。

    - 保存形式: JSON Lines（1行1メッセージ、追記専用）
    - セッションごとにファイル分離（YYYY-MM-DD_HHMMSSRR.jsonl）
    - トークン詰め: max_tokens を超えたら古いメッセージから除外
    - プレースホルダーはメモリ上のみ。確定後に1回追記
    """

    def __init__(
        self,
        sessions_dir: Path,
        persona_id: str,
        max_tokens: int = 32000,
        save_interval: int = 1,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.persona_id = persona_id
        self.max_tokens = max_tokens
        self.save_interval = save_interval
        self._messages: list[dict] = []
        self._system_messages: list[dict] = []
        self._turn_count = 0
        self._session_id: str = ""

    @property
    def session_id(self) -> str:
        return self._session_id

    def set_session_id(self, sid: str):
        """セッションIDを設定。最初の保存前に呼ぶ。"""
        self._session_id = sid

    @property
    def persona_dir(self) -> Path:
        return self.sessions_dir / self.persona_id

    @property
    def session_file(self) -> Path:
        """セッションのJSONLファイルパス（常に新形式）。"""
        today = time.strftime("%Y-%m-%d")
        sid = self._session_id or time.strftime("%H%M%S") + "00"
        return self.persona_dir / f"{today}_{sid}.jsonl"

    @property
    def today_file(self) -> Path:
        """session_file のエイリアス。"""
        return self.session_file

    def set_system_prompt(self, system_messages: list[dict]):
        """システムプロンプトを設定。履歴の先頭に固定で付与される。"""
        self._system_messages = system_messages

    def get_context(self) -> list[dict]:
        """APIに渡すメッセージリスト（システムプロンプト + 会話履歴）を返す。"""
        return self._system_messages + self._messages

    def add(self, user_text: str, assistant_text: str):
        """ユーザーとアシスタントのメッセージを1往復追加。

        ファイルには書き込まない。プレースホルダーとして空文字を許容し、
        後で上書きする運用を前提とする。
        """
        self._messages.append({"role": "user", "content": user_text})
        self._messages.append({"role": "assistant", "content": assistant_text})
        self._turn_count += 1

    def save_turn(self):
        """確定した直近1往復（2メッセージ）をファイル末尾に追記する。

        プレースホルダーが本文で上書きされた後に呼ばれることを想定。
        追記専用のため、ディスクI/Oは最小限。
        不正なペア（user/user 等）を検出した場合は _save_full() にフォールバック。
        """
        if len(self._messages) < 2:
            return
        last_two = self._messages[-2:]
        if last_two[0].get("role") != "user" or last_two[1].get("role") != "assistant":
            # 不正なペア → 全保存で修復
            self._save_full()
            return
        self.persona_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(m, ensure_ascii=False) for m in last_two]
        with open(self.today_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def trim(self):
        """トークン数が max_tokens を超えていたら古いメッセージを除外する。

        簡易推定: 1文字 ≒ 1.5トークン（日本語のため係数1.5）。英語のみなら1.3。
        ファイル側は追記専用のため削除しない。読み込み時に trim が適用される。
        """
        # システムプロンプトのトークン数
        system_chars = sum(len(m.get("content", "")) for m in self._system_messages)
        system_tokens = int(system_chars * 1.5)

        # 許容される会話履歴のトークン数
        allowed_tokens = self.max_tokens - system_tokens
        if allowed_tokens <= 0:
            return

        # 新しい方から数えて、トークン制限に収まるまで保持
        kept = []
        total_tokens = 0
        for msg in reversed(self._messages):
            msg_tokens = int(len(msg.get("content", "")) * 1.5)
            if total_tokens + msg_tokens > allowed_tokens:
                break
            kept.insert(0, msg)
            total_tokens += msg_tokens

        self._messages = kept

    def reload(self, persona_id: str):
        """ペルソナ切替時に呼ばれる。履歴を空にする（続きからは resume を使う）。"""
        self.persona_id = persona_id
        self._messages = []
        self._turn_count = 0

    def _load_specific(self, jsonl_path):
        """指定されたJSONLファイルから履歴を読み込む（セッション再開用）。"""
        self._messages = []
        self._turn_count = 0
        if not jsonl_path.exists():
            return
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    self._messages.append(msg)
            self._trim()
            self._turn_count = sum(1 for m in self._messages if m.get("role") == "user")
        except Exception:
            pass

    def update_message(self, index: int, content: str):
        """指定インデックスのメッセージ内容を更新し、JSONLを全書き直し。"""
        if 0 <= index < len(self._messages):
            self._messages[index]["content"] = content
            self._save_full()

    def _save_full(self):
        """全メッセージをJSONLファイルに書き出す（アトミック上書き）。
        
        一時ファイルに書き込んでからリネームすることで、
        書き込み中にクラッシュしても元ファイルが残る。
        """
        self.persona_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(m, ensure_ascii=False) for m in self._messages]
        temp_file = self.today_file.with_suffix(".jsonl.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(temp_file, self.today_file)

    def _load_latest(self):
        """最新の履歴ファイルから直近のメッセージを読み込む。"""
        if not self.persona_dir.exists():
            return

        # 日付降順でファイルを探す
        jsonl_files = sorted(
            self.persona_dir.glob("*.jsonl"),
            reverse=True,
        )
        if not jsonl_files:
            return

        # 最新ファイルから読み込み
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                self._messages.append(msg)
            except json.JSONDecodeError:
                continue

        # トークン制限を適用
        self.trim()

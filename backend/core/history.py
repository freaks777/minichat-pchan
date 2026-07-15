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
        self._saved_message_count = 0
        self._session_id: str = ""
        self._session_date: str = ""  # セッション作成日（YYYY-MM-DD）、未設定時は今日

    @property
    def session_id(self) -> str:
        return self._session_id

    def set_session_id(self, sid: str, date: str = ""):
        """セッションIDと日付を設定。date未指定時は今日。"""
        self._session_id = sid
        self._session_date = date if date else time.strftime("%Y-%m-%d")

    @property
    def persona_dir(self) -> Path:
        return self.sessions_dir / self.persona_id

    @property
    def session_file(self) -> Path:
        """セッションのJSONLファイルパス。"""
        date = self._session_date or time.strftime("%Y-%m-%d")
        sid = self._session_id or time.strftime("%H%M%S") + "00"
        return self.persona_dir / f"{date}_{sid}.jsonl"

    @property
    def today_file(self) -> Path:
        """session_file のエイリアス。"""
        return self.session_file

    def set_system_prompt(self, system_messages: list[dict]):
        """システムプロンプトを設定。履歴の先頭に固定で付与される。"""
        self._system_messages = system_messages

    def get_context(self) -> list[dict]:
        """Return a non-destructively trimmed context for the API."""
        return self._system_messages + self._messages_for_context()

    def _messages_for_context(self) -> list[dict]:
        """Keep recent complete turns without deleting the full in-memory history."""
        system_chars = sum(len(m.get("content", "")) for m in self._system_messages)
        allowed_tokens = max(0, self.max_tokens - int(system_chars * 1.5))
        kept: list[dict] = []
        total_tokens = 0
        i = len(self._messages) - 1

        while i >= 0:
            if (self._messages[i].get("role") == "assistant" and i > 0
                    and self._messages[i - 1].get("role") == "user"):
                group = self._messages[i - 1:i + 1]
                i -= 2
            else:
                group = [self._messages[i]]
                i -= 1

            group_tokens = sum(
                int(len(m.get("content", "")) * 1.5) for m in group
            )
            if kept and total_tokens + group_tokens > allowed_tokens:
                break
            kept = group + kept
            total_tokens += group_tokens
            if total_tokens >= allowed_tokens:
                break

        return kept

    def add(self, user_text: str, assistant_text: str):
        """ユーザーとアシスタントのメッセージを1往復追加。

        ファイルには書き込まない。プレースホルダーとして空文字を許容し、
        後で上書きする運用を前提とする。
        """
        self._messages.append({"role": "user", "content": user_text})
        self._messages.append({"role": "assistant", "content": assistant_text})
        self._turn_count += 1

    def save_turn(self, force: bool = False):
        """未保存の確定ターンを、設定された間隔でファイル末尾に追記する。

        force=True は終了・中断・エラー時のフラッシュ用。
        追記専用のため、ディスクI/Oは最小限。
        不正なペアを検出した場合は _save_full() にフォールバック。
        """
        pending = self._messages[self._saved_message_count:]
        if not pending:
            return

        try:
            interval = max(1, int(self.save_interval))
        except (TypeError, ValueError):
            interval = 1
        if not force and self._turn_count % interval != 0:
            return

        valid_pairs = len(pending) % 2 == 0 and all(
            pending[i].get("role") == "user"
            and pending[i + 1].get("role") == "assistant"
            for i in range(0, len(pending), 2)
        )
        if not valid_pairs:
            self._save_full()
            return

        self.persona_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(m, ensure_ascii=False) for m in pending]
        with open(self.today_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        self._saved_message_count = len(self._messages)

    def trim(self):
        """Destructively trim to the same complete-turn context used by the API."""
        self._messages = self._messages_for_context()

    def reload(self, persona_id: str):
        """ペルソナ切替時に呼ばれる。履歴を空にする（続きからは resume を使う）。"""
        self.persona_id = persona_id
        self._messages = []
        self._turn_count = 0
        self._saved_message_count = 0

    def _load_specific(self, jsonl_path):
        """指定されたJSONLファイルから履歴を読み込む（セッション再開用）。"""
        self._messages = []
        self._turn_count = 0
        self._saved_message_count = 0
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
            self._turn_count = sum(1 for m in self._messages if m.get("role") == "user")
            self._saved_message_count = len(self._messages)
        except Exception:
            # Do not disguise a corrupt history as an empty successful load.
            # Let the caller handle the restoration failure.
            raise

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
        self._saved_message_count = len(self._messages)

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
        self._saved_message_count = len(self._messages)

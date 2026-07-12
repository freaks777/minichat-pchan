"""セッションコンテキスト。hook 間で共有されるデータ構造。

dict のまま育てるとキー名衝突が避けられないため、
軽量な dataclass で管理する。プラグイン固有のデータは extras に格納。
"""

from dataclasses import dataclass, field


@dataclass
class SessionContext:
    """1セッションの会話コンテキスト。hook 間で共有される。

    persona_id / style / history はコア管理（プラグインは読み取りのみ）。
    extras はプラグイン用の自由領域。
    user_input は on_user_message 以降で有効。
    """

    # コア管理
    persona_id: str
    style: dict
    history: object  # 'History'（循環import回避）

    # プラグイン用拡張領域
    extras: dict = field(default_factory=dict)

    # ユーザー入力（on_user_message 以降で有効）
    user_input: str = ""

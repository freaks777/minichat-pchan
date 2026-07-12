"""プラグイン基底クラス。"""

from abc import ABC, abstractmethod


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
    async def run(self, hook: str, data, ctx):
        """data を処理し、必要なら書き換えて返す。書き換え不要なら None を返す。"""
        ...

    def get_ui_slot(self) -> dict | None:
        """フロントに追加するUI要素の定義を返す。なければ None。

        戻り値はUIの「定義」（ボタン・パネル・設定項目等の構造データ）であり、
        HTML文字列ではない。実際の表示方法はフロントエンド実装時に決定する。
        例: {"type": "button", "id": "voice-toggle", "label": "🎤"}
        """
        return None

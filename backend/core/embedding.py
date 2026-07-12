"""埋め込みプロバイダ。ローカルモデルとAPIの差し替えを可能にする抽象層。"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("rp-standalone")

class EmbeddingProvider(ABC):
    """埋め込み生成のインターフェース。"""

    @abstractmethod
    def encode(self, texts: list[str]) -> list[list[float]]:
        """テキストリストを埋め込みベクトルリストに変換する。"""
        ...

    @abstractmethod
    def encode_query(self, text: str) -> list[float]:
        """検索クエリ用の単一埋め込みを生成する。"""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """埋め込みベクトルの次元数。"""
        ...


class SentenceTransformersProvider(EmbeddingProvider):
    """sentence-transformers を使ったローカル埋め込み。

    e5系モデルはクエリ時に "query: " プレフィックス、
    保存時に "passage: " プレフィックスを付与する規約がある。
    """

    def __init__(self, model_name: str, cache_folder: str | None = None):
        import os
        import warnings
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        warnings.filterwarnings("ignore", message=".*unauthenticated.*")
        from sentence_transformers import SentenceTransformer

        if cache_folder:
            self._model = SentenceTransformer(model_name, cache_folder=cache_folder)
        else:
            self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()  # get_embedding_dimension in newer versions

    def encode(self, texts: list[str]) -> list[list[float]]:
        prefixed = [f"passage: {t}" for t in texts]
        embeddings = self._model.encode(prefixed, normalize_embeddings=True)
        return embeddings.tolist()

    def encode_query(self, text: str) -> list[float]:
        emb = self._model.encode(
            f"query: {text}",
            normalize_embeddings=True,
        )
        return emb.tolist()

    @property
    def dimension(self) -> int:
        return self._dim

    def unload(self):
        """モデルをメモリから明示的に解放する。"""
        if hasattr(self, "_model") and self._model is not None:
            model_name = self._model.__class__.__name__
            del self._model
            self._model = None
            import gc
            gc.collect()
            logger.info("embedding: unloaded %s model from memory", model_name)

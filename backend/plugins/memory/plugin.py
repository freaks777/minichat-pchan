"""memory プラグイン — ChromaDB を使った長期記憶（RAG）。

on_session_end: 会話履歴からLLMが重要事実を抽出 → embed → ChromaDB保存。
on_build_context: ユーザー入力から類似記憶を検索 → システムプロンプトに注入。
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from pathlib import Path

from plugins.base import PluginBase

logger = logging.getLogger("rp-standalone")

# 事実抽出プロンプト
EXTRACT_FACTS_PROMPT = """以下の会話ログから、長期的に覚えておくべき重要な事実だけを抽出せよ。
各行に1つの事実を「〜は〜である」「〜が〜した」の形式で簡潔に書け。
些細なやりとりや挨拶は除外すること。最大10件。

会話ログ:
{conversation}

事実:"""

LEADING_BULLET_RE = re.compile(
    r"^\s*(?:(?:[・○\-*+])\s*|(?:\d+[.．)]\s*))+"
)


def normalize_fact(fact: str) -> str:
    """重複比較用に表記揺れだけを保守的に正規化する。"""
    normalized = unicodedata.normalize("NFKC", fact)
    normalized = LEADING_BULLET_RE.sub("", normalized)
    return " ".join(normalized.split()).strip()


def deduplicate_facts(facts: list[str], existing: list[str] | None = None) -> list[str]:
    """既存文書と入力順を維持しながら完全一致の重複を除外する。"""
    seen = {normalize_fact(fact) for fact in (existing or [])}
    unique = []
    for fact in facts:
        normalized = normalize_fact(fact)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def fact_id(persona_id: str, session_id: str, fact: str) -> str:
    """同一ペルソナ・セッション・事実に決定的なIDを割り当てる。"""
    separator = chr(0)
    source = separator.join((persona_id, session_id, normalize_fact(fact)))
    return "fact_" + hashlib.sha256(source.encode("utf-8")).hexdigest()


class MemoryPlugin(PluginBase):
    name = "memory"
    hooks = ["on_build_context", "on_session_end"]
    priority = 50  # context構築の先頭で注入
    critical = False

    def __init__(self):
        self._embedding_provider = None     # EmbeddingProvider
        self._chroma_client = None          # chromadb.PersistentClient
        self._collection = None             # chromadb.Collection
        self._config = None                 # API設定（事実抽出用）

    def configure(
        self,
        embedding_provider,
        chroma_path: str,
        config: dict,
    ):
        """依存オブジェクトの注入。main.py 起動時に呼ばれる。"""
        self._embedding_provider = embedding_provider
        self._config = config

        import chromadb
        self._chroma_client = chromadb.PersistentClient(path=chroma_path)
        self._collection = self._chroma_client.get_or_create_collection(
            name="rp_memory",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "memory: ChromaDB ready (dim=%d, path=%s)",
            self._embedding_provider.dimension,
            chroma_path,
        )

    async def run(self, hook: str, data, ctx):
        if hook == "on_session_end":
            await self._on_session_end(ctx)
        elif hook == "on_build_context":
            data = await self._on_build_context(data, ctx)
            return data
        return None

    async def shutdown(self):
        """ChromaDB クライアントを切断し、埋め込みモデルを解放する。"""
        if self._embedding_provider is not None:
            try:
                self._embedding_provider.unload()
            except Exception as e:
                logger.error("memory: embedding unload failed (%s)", e)
            self._embedding_provider = None

        if self._chroma_client is not None:
            try:
                self._collection = None
                self._chroma_client = None
                import gc
                gc.collect()
                logger.info("memory: ChromaDB connection released")
            except Exception as e:
                logger.error("memory: shutdown error (%s)", e)

    # ── 保存 ──────────────────────────────────────────────────

    async def _on_session_end(self, ctx):
        """会話終了時に重要事実を抽出して保存。"""
        if self._collection is None or self._config is None:
            logger.warning("memory: not configured, skipping save")
            return

        history = ctx.history
        messages = getattr(history, "_messages", [])
        if not messages:
            return

        # 会話テキストを構築（直近20往復まで）
        lines = []
        for msg in messages[-40:]:  # 最大20往復
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                continue
            prefix = "ユーザー" if role == "user" else "キャラクター"
            lines.append(f"{prefix}: {content}")

        conversation = "\n".join(lines)
        if len(conversation) < 20:
            return  # 短すぎる会話はスキップ

        # LLMで事実抽出
        prompt = EXTRACT_FACTS_PROMPT.format(conversation=conversation[-6000:])
        messages_for_llm = [{"role": "user", "content": prompt}]

        from core.api import chat_sync
        import copy
        api_config = copy.deepcopy(self._config)
        api_config["api"]["max_tokens"] = 500

        try:
            result = await chat_sync(messages_for_llm, api_config)
        except Exception as e:
            logger.error("memory: fact extraction failed (%s)", e)
            return

        # 事実をパース。同一抽出結果内の重複はDB照合前に除外する。
        facts = deduplicate_facts(result.split("\n"))
        facts = [f for f in facts if len(f) > 5 and "事実" not in f]
        if not facts:
            logger.info("memory: no facts extracted")
            return

        persona_id = ctx.persona_id
        session_id = getattr(ctx.history, "session_id", "") or "unknown"
        await self._store_facts(facts, persona_id, session_id)

    async def _store_facts(
        self,
        facts: list[str],
        persona_id: str,
        session_id: str,
    ) -> int:
        """同一セッションの既存文書を除外し、新規事実だけを保存する。"""
        existing_documents = []
        where = {"$and": [
            {"persona_id": persona_id},
            {"session_id": session_id},
        ]}
        try:
            result = await asyncio.to_thread(
                self._collection.get,
                where=where,
                include=["documents"],
            )
            existing_documents = result.get("documents", []) or []
        except Exception as e:
            # 決定的ID + upsertにより新形式データの重複は防げるため保存は継続する。
            logger.error("memory: existing facts lookup failed (%s)", e)

        new_facts = deduplicate_facts(facts, existing_documents)
        if not new_facts:
            logger.info(
                "memory: skipped duplicate facts for %s/%s",
                persona_id,
                session_id,
            )
            return 0

        try:
            embeddings = await asyncio.to_thread(
                self._embedding_provider.encode,
                new_facts,
            )
        except Exception as e:
            logger.error("memory: embedding failed (%s)", e)
            return 0

        ts = time.time()
        ids = [fact_id(persona_id, session_id, fact) for fact in new_facts]
        metadatas = [
            {"persona_id": persona_id, "session_id": session_id, "timestamp": ts}
            for _ in new_facts
        ]

        try:
            await asyncio.to_thread(
                self._collection.upsert,
                ids=ids,
                embeddings=embeddings,
                documents=new_facts,
                metadatas=metadatas,
            )
            logger.info(
                "memory: stored %d new facts for %s",
                len(new_facts),
                persona_id,
            )
            return len(new_facts)
        except Exception as e:
            logger.error("memory: ChromaDB upsert failed (%s)", e)
            return 0

    # ── 検索 ──────────────────────────────────────────────────

    async def _on_build_context(self, messages: list[dict], ctx):
        """ユーザー入力から類似記憶を検索し、システムプロンプトに注入。"""
        if self._collection is None:
            return messages

        user_input = ctx.user_input
        if not user_input or len(user_input) < 3:
            return messages

        try:
            query_emb = await asyncio.to_thread(
                self._embedding_provider.encode_query, user_input
            )
        except Exception as e:
            logger.error("memory: query embedding failed (%s)", e)
            return messages

        try:
            # memory_scope に応じてフィルタ切替
            scope = getattr(ctx, "memory_scope", "session")
            sid = getattr(ctx.history, "session_id", "") or ""
            if scope == "session" and sid:
                where = {"$and": [
                    {"persona_id": ctx.persona_id},
                    {"session_id": sid},
                ]}
            else:
                where = {"persona_id": ctx.persona_id}
            results = await asyncio.to_thread(
                self._collection.query,
                query_embeddings=[query_emb],
                n_results=3,
                where=where,
            )
        except Exception as e:
            logger.error("memory: ChromaDB query failed (%s)", e)
            return messages

        docs = results.get("documents", [[]])[0]
        if not docs:
            return messages

        # 記憶を注入
        memory_text = "## 関連する記憶\n" + "\n".join(f"- {d}" for d in docs)
        memory_msg = {"role": "system", "content": memory_text}
        logger.debug("memory: injected %d facts for query", len(docs))

        # システムプロンプトの直後に注入
        # systemメッセージの次に差し込む
        insert_at = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_at = i + 1
        return messages[:insert_at] + [memory_msg] + messages[insert_at:]

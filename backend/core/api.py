"""API 呼び出し — マルチプロバイダ対応（ストリーミング / 同期）。

サポートするプロバイダ:
  - OpenAI互換（openrouter / openai 等）: /chat/completions + Bearer
  - Anthropic: /messages + x-api-key（Messages API）
  - Google: :streamGenerateContent + API key（Gemini API）
"""

from typing import AsyncGenerator

import httpx


# ── プロバイダ解決 ──────────────────────────────────────────────

def _resolve(config: dict) -> tuple[dict, str]:
    """config.yaml から active_provider / active_model を解決し、
    (provider設定, モデル名) を返す。
    """
    provider_id = config.get("active_provider", "")
    model = config.get("active_model", "")
    providers = config.get("providers", {})

    if provider_id not in providers:
        raise ValueError(f"provider '{provider_id}' not found in config.providers")

    provider = dict(providers[provider_id])
    return provider, model


# ── エントリポイント ────────────────────────────────────────────

async def chat_stream(
    messages: list[dict],
    config: dict,
    model_info: dict | None = None,
) -> AsyncGenerator[str, None]:
    """プロバイダを自動判定してストリーミング応答を yield する。

    Args:
        messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
        config: config.yaml 全体
        model_info: 指定すると {"requested": ..., "actual": ...} が書き込まれる
    """
    provider, model = _resolve(config)
    api_type = provider.get("api_type", "")

    if model_info is not None:
        model_info["requested"] = model

    if api_type == "anthropic":
        async for chunk in _anthropic_stream(messages, provider, model, config, model_info):
            yield chunk
    elif api_type == "google":
        async for chunk in _google_stream(messages, provider, model, config, model_info):
            yield chunk
    else:
        # デフォルト: OpenAI互換
        async for chunk in _openai_stream(messages, provider, model, config, model_info):
            yield chunk


async def chat_sync(
    messages: list[dict],
    config: dict,
) -> str:
    """非ストリーミング版。"""
    provider, model = _resolve(config)
    api_type = provider.get("api_type", "")

    if api_type == "anthropic":
        return await _anthropic_sync(messages, provider, model, config)
    elif api_type == "google":
        return await _google_sync(messages, provider, model, config)
    else:
        return await _openai_sync(messages, provider, model, config)


# ── OpenAI互換（OpenRouter / OpenAI 等） ─────────────────────────

def _openai_headers(provider: dict) -> dict:
    return {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }


async def _openai_stream(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
    model_info: dict | None,
) -> AsyncGenerator[str, None]:
    api_cfg = config.get("api", {})
    base_url = provider["base_url"]

    # OpenRouter 用ヘッダー
    headers = _openai_headers(provider)
    if "openrouter" in base_url:
        headers["HTTP-Referer"] = "http://localhost:8765"
        headers["X-Title"] = "RP Standalone"

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": api_cfg.get("max_tokens", 2000),
        "temperature": api_cfg.get("temperature", 0.8),
        "stream": True,
    }
    timeout = httpx.Timeout(api_cfg.get("timeout", 120))

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                import json
                try:
                    chunk = json.loads(data)
                    if model_info is not None and model_info.get("actual") is None:
                        model_info["actual"] = chunk.get("model")
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


async def _openai_sync(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
) -> str:
    api_cfg = config.get("api", {})
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": api_cfg.get("max_tokens", 2000),
        "temperature": api_cfg.get("temperature", 0.8),
        "stream": False,
    }
    timeout = httpx.Timeout(api_cfg.get("timeout", 120))

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider['base_url']}/chat/completions",
            headers=_openai_headers(provider),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            finish = data["choices"][0].get("finish_reason", "unknown")
            import logging
            logging.getLogger("rp_standalone").warning(
                "API returned empty content. finish_reason=%s, model=%s", finish, model
            )
        return content


# ── Anthropic Messages API ────────────────────────────────────────

def _anthropic_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """OpenAI形式のメッセージを Anthropic 形式に変換。
    Returns: (system_text, user/assistant messages)
    """
    system_text = None
    converted = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            system_text = content
        elif role == "assistant":
            converted.append({"role": "assistant", "content": content})
        elif role == "user":
            converted.append({"role": "user", "content": content})
    return system_text, converted


async def _anthropic_stream(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
    model_info: dict | None,
) -> AsyncGenerator[str, None]:
    api_cfg = config.get("api", {})
    system_text, converted = _anthropic_messages(messages)

    headers = {
        "x-api-key": provider["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": api_cfg.get("max_tokens", 2000),
        "messages": converted,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text

    timeout = httpx.Timeout(api_cfg.get("timeout", 120))

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{provider['base_url']}/messages",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                import json
                try:
                    event = json.loads(data)
                    if model_info is not None and model_info.get("actual") is None:
                        model_info["actual"] = event.get("model")
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text
                except (json.JSONDecodeError, KeyError):
                    continue


async def _anthropic_sync(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
) -> str:
    api_cfg = config.get("api", {})
    system_text, converted = _anthropic_messages(messages)

    headers = {
        "x-api-key": provider["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": api_cfg.get("max_tokens", 2000),
        "messages": converted,
    }
    if system_text:
        payload["system"] = system_text

    timeout = httpx.Timeout(api_cfg.get("timeout", 120))

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider['base_url']}/messages",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


# ── Google Gemini API ─────────────────────────────────────────────

def _gemini_contents(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """OpenAI形式のメッセージを Gemini 形式に変換。
    Returns: (system_text, contents list)
    """
    system_text = None
    contents = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "system":
            system_text = content
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
    return system_text, contents


async def _google_stream(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
    model_info: dict | None,
) -> AsyncGenerator[str, None]:
    api_cfg = config.get("api", {})
    api_key = provider["api_key"]
    system_text, contents = _gemini_contents(messages)

    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": api_cfg.get("max_tokens", 2000),
            "temperature": api_cfg.get("temperature", 0.8),
        },
    }
    if system_text:
        payload["systemInstruction"] = {
            "parts": [{"text": system_text}],
        }

    timeout = httpx.Timeout(api_cfg.get("timeout", 120))
    url = f"{provider['base_url']}/models/{model}:streamGenerateContent?alt=sse&key={api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                import json
                try:
                    chunk = json.loads(data)
                    if model_info is not None and model_info.get("actual") is None:
                        model_info["actual"] = model
                    candidates = chunk.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


async def _google_sync(
    messages: list[dict],
    provider: dict,
    model: str,
    config: dict,
) -> str:
    api_cfg = config.get("api", {})
    api_key = provider["api_key"]
    system_text, contents = _gemini_contents(messages)

    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": api_cfg.get("max_tokens", 2000),
            "temperature": api_cfg.get("temperature", 0.8),
        },
    }
    if system_text:
        payload["systemInstruction"] = {
            "parts": [{"text": system_text}],
        }

    timeout = httpx.Timeout(api_cfg.get("timeout", 120))
    url = f"{provider['base_url']}/models/{model}:generateContent?key={api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

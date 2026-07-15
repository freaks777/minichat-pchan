"""API 呼び出し — マルチプロバイダ対応（ストリーミング / 同期）。

サポートするプロバイダ:
  - OpenAI互換（openrouter / openai 等）: /chat/completions + Bearer
  - Anthropic: /messages + x-api-key（Messages API）
  - Google: :streamGenerateContent + API key（Gemini API）

デバッグ: API_DEBUG_DUMP=1 環境変数で生APIレスポンスを logs/api_debug/ に保存。
          content 空等の異常系は環境変数に関わらず常に保存。
"""

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import AsyncGenerator

import httpx
import logging
import json


# ── プロバイダ解決 ──────────────────────────────────────────────

def _provider_id_from_url(base_url: str) -> str:
    """base_url からプロバイダ識別子を推測。"""
    if "openrouter" in base_url:
        return "openrouter"
    elif "opencode.ai/zen/go" in base_url:
        return "opencode-go"
    elif "opencode.ai/zen" in base_url:
        return "opencode-zen"
    elif "openai.com" in base_url:
        return "openai"
    elif "anthropic.com" in base_url:
        return "anthropic"
    elif "googleapis.com" in base_url:
        return "google"
    elif "deepseek.com" in base_url:
        return "deepseek"
    elif "bigmodel.cn" in base_url:
        return "glm"
    elif "x.ai" in base_url:
        return "xai"
    return base_url.split("://")[-1].split("/")[0]  # fallback: hostname


def _dump_api_debug(
    provider_id: str,
    model: str,
    request_payload: dict,
    response_status: int,
    response_headers: dict,
    response_body: dict,
    elapsed_ms: float,
    *,
    force: bool = False,
) -> str | None:
    """生APIレスポンスをファイルに保存。

    force=False → API_DEBUG_DUMP=1 時のみ
    force=True  → 常に保存（content空等の異常系）
    """
    if not force and not os.environ.get("API_DEBUG_DUMP"):
        return None

    output_dir = Path(__file__).parent.parent / "logs" / "api_debug"
    output_dir.mkdir(parents=True, exist_ok=True)

    JST = timezone(timedelta(hours=9))
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S_%f")
    safe_model = model.replace("/", "_").replace(":", "_")
    filename = f"{ts}_{provider_id}_{safe_model}.json"
    filepath = output_dir / filename

    # メッセージ内容は長大になりうるため要約
    safe_payload = json.loads(json.dumps(request_payload))
    if "messages" in safe_payload:
        msgs = safe_payload["messages"]
        safe_payload["_messages_summary"] = {
            "count": len(msgs),
            "roles": [m.get("role", "") for m in msgs],
            "content_lengths": [len(m.get("content", "")) for m in msgs],
        }
        del safe_payload["messages"]

    dump = {
        "timestamp": datetime.now(JST).isoformat(),
        "provider": provider_id,
        "model": model,
        "request": {
            "payload": safe_payload,
        },
        "response": {
            "status_code": response_status,
            "headers": {k: v for k, v in response_headers.items()},
            "elapsed_ms": round(elapsed_ms, 1),
            "body": response_body,
        },
    }

    # content / reasoning_content の自動分析 + トップレベルサマリ（一覧スキャン用）
    choices = response_body.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        finish = choices[0].get("finish_reason", "?")
        usage = response_body.get("usage", {})
        reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

        dump["analysis"] = {
            "content_length": len(content) if content else 0,
            "reasoning_len": len(reasoning) if reasoning else 0,
            "finish_reason": finish,
            "has_content": bool(content and content.strip()),
            "usage": usage,
        }

        # トップレベルサマリ: ファイルを開かずに一覧で傾向を掴む用
        dump["summary"] = {
            "finish_reason": finish,
            "has_content": bool(content and content.strip()),
            "content_len": len(content) if content else 0,
            "reasoning_len": len(reasoning) if reasoning else 0,
            "elapsed_ms": round(elapsed_ms, 1),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": reasoning_tokens,
            "reasoning_pct": round(reasoning_tokens / max(usage.get("completion_tokens", 1), 1) * 100, 1),
        }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)

    logging.getLogger("rp-standalone").info(
        "API debug dump → %s (status=%d, content=%d, reasoning=%d)",
        filepath, response_status,
        dump.get("analysis", {}).get("content_length", 0),
        dump.get("analysis", {}).get("reasoning_len", 0),
    )
    return str(filepath)



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

    reasoning_buffer = ""
    has_content = False

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
                    # DeepSeek推論モデル: reasoning_content（CoT）は表示せずバッファに溜める
                    # content が一度も来なかった場合のみ最終フォールバックとして使う
                    reasoning = delta.get("reasoning_content", "")
                    if reasoning:
                        reasoning_buffer += reasoning
                    if content:
                        has_content = True
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError) as e:
                    logging.getLogger("rp-standalone").warning(
                        "API chunk parse error (openai): %s", e
                    )
                    continue

            # content が一度も来なかった場合、reasoning をフォールバック
            if not has_content and reasoning_buffer:
                yield reasoning_buffer


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

    t_start = time.time()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{provider['base_url']}/chat/completions",
            headers=_openai_headers(provider),
            json=payload,
        )
        elapsed_ms = (time.time() - t_start) * 1000
        resp.raise_for_status()
        data = resp.json()

        provider_id = _provider_id_from_url(provider["base_url"])

        # レスポンス構造の検証
        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            logging.getLogger("rp-standalone").error(
                "API unexpected response: no choices. model=%s, keys=%s, raw=%s",
                model, list(data.keys()), json.dumps(data, ensure_ascii=False)[:500],
            )
            _dump_api_debug(
                provider_id, model,
                payload, resp.status_code, resp.headers, data, elapsed_ms, force=True,
            )
            return ""

        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        # DeepSeek推論モデル: content が空で reasoning_content にのみ出力される場合がある
        # 抽出などの構造化出力では reasoning_content（CoT）は不正なJSONのため、
        # フォールバックせず空文字を返す。異常時は生レスポンスを自動でファイル保存する。
        if not content or not content.strip():
            finish = data["choices"][0].get("finish_reason", "unknown")
            logging.getLogger("rp-standalone").warning(
                "API empty content. finish_reason=%s, model=%s, msg_keys=%s, "
                "reasoning_len=%d",
                finish, model,
                list(msg.keys()),
                len(msg.get("reasoning_content") or ""),
            )
            _dump_api_debug(
                provider_id, model,
                payload, resp.status_code, resp.headers, data, elapsed_ms, force=True,
            )
        else:
            # API_DEBUG_DUMP=1 時のみ通常レスポンスも保存
            _dump_api_debug(
                provider_id, model,
                payload, resp.status_code, resp.headers, data, elapsed_ms,
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
                except (json.JSONDecodeError, KeyError) as e:
                    logging.getLogger("rp-standalone").warning(
                        "API chunk parse error (anthropic): %s", e
                    )
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
                except (json.JSONDecodeError, KeyError, IndexError) as e:
                    logging.getLogger("rp-standalone").warning(
                        "API chunk parse error (google): %s", e
                    )
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

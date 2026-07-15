"""APIデバッグダンプツール — DeepSeek V4 Pro + OpenCode Go の生レスポンスを取得。

使い方:
  python debug_dump_api.py                          # 簡易テスト
  python debug_dump_api.py --extract "テキスト..."   # 抽出APIの再現テスト

出力: backend/logs/api_debug/{timestamp}_{provider}_{model}.json
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# カレントディレクトリを backend/ に設定
os.chdir(Path(__file__).parent)

# .env 読込
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# config.yaml 読込
from ruamel.yaml import YAML
yaml = YAML()
yaml.preserve_quotes = True
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.load(f)

# 環境変数解決 ${VAR}
import re
def _resolve_env(value):
    if isinstance(value, str):
        return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), ''), value)
    return value

def resolve_config(obj):
    if isinstance(obj, dict):
        return {k: resolve_config(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [resolve_config(v) for v in obj]
    else:
        return _resolve_env(obj)

config = resolve_config(config)

import httpx

OUTPUT_DIR = Path(__file__).parent / "logs" / "api_debug"
JST = timezone(timedelta(hours=9))


def dump_response(
    provider_id: str,
    model: str,
    request_payload: dict,
    response,
    response_body: dict,
    elapsed_ms: float,
    note: str = "",
):
    """生APIレスポンスをファイルに保存。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{provider_id}_{model.replace('/', '_')}.json"
    filepath = OUTPUT_DIR / filename

    # 機密情報マスク
    safe_payload = json.loads(json.dumps(request_payload))
    if "messages" in safe_payload:
        for m in safe_payload["messages"]:
            if len(m.get("content", "")) > 500:
                m["content"] = m["content"][:500] + f"... [truncated, total {len(m['content'])} chars]"

    dump = {
        "timestamp": datetime.now(JST).isoformat(),
        "provider": provider_id,
        "model": model,
        "note": note,
        "request": {
            "url": str(response.request.url) if hasattr(response, 'request') else "unknown",
            "headers": {k: v for k, v in safe_payload.get("headers", {}).items()} if "headers" in safe_payload else {},
            "payload": {
                "model": safe_payload.get("model"),
                "max_tokens": safe_payload.get("max_tokens"),
                "temperature": safe_payload.get("temperature"),
                "stream": safe_payload.get("stream"),
                "messages_count": len(safe_payload.get("messages", [])),
                "messages": safe_payload.get("messages", []),
            },
        },
        "response": {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "elapsed_ms": round(elapsed_ms, 1),
            "body": response_body,
        },
        "analysis": {},
    }

    # 自動分析
    choices = response_body.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        finish = choices[0].get("finish_reason", "unknown")
        dump["analysis"] = {
            "content_length": len(content) if content else 0,
            "content_preview": (content[:300] if content else "") if content else "",
            "reasoning_content_length": len(reasoning) if reasoning else 0,
            "reasoning_content_preview": (reasoning[:300] if reasoning else "") if reasoning else "",
            "finish_reason": finish,
            "has_content": bool(content and content.strip()),
            "has_reasoning": bool(reasoning and reasoning.strip()),
        }

    # usage 情報
    usage = response_body.get("usage", {})
    if usage:
        dump["analysis"]["usage"] = usage

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)

    print(f"DEBUG DUMP → {filepath}")
    print(f"  status={response.status_code}, content_len={dump['analysis'].get('content_length', 0)}, "
          f"reasoning_len={dump['analysis'].get('reasoning_content_length', 0)}, "
          f"finish={dump['analysis'].get('finish_reason', '?')}")
    return filepath


async def test_simple():
    """簡易テスト: 短いプロンプトで API 呼び出し。"""
    provider = config["providers"].get("opencode-go")
    if not provider:
        print("ERROR: opencode-go provider not found in config.yaml")
        return

    model = "deepseek-v4-pro"
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "「こんにちは」とだけ返してください。他の出力は一切不要です。"}
        ],
        "max_tokens": 100,
        "temperature": 0.0,
        "stream": False,
    }

    print(f"\n=== Test 1: Simple prompt ({model} via opencode-go) ===\n")
    start = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
        resp = await client.post(
            f"{provider['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {provider['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        elapsed = (time.time() - start) * 1000
        resp.raise_for_status()
        body = resp.json()

    dump_response("opencode-go", model, payload, resp, body, elapsed, note="simple test")
    return body


async def test_extraction():
    """抽出APIの再現テスト: 長めのプロンプトで複数フィールド抽出。"""
    provider = config["providers"].get("opencode-go")
    if not provider:
        print("ERROR: opencode-go provider not found in config.yaml")
        return

    # 実際の抽出プロンプトに近い内容
    from plugins.persona_studio.plugin import _build_extraction_prompt, _EXTRACTION_FIELDS

    sample_text = """
名前: 山田太郎
性別: 男性
年齢: 28歳
誕生日: 1998年3月15日
血液型: A型
身長: 175cm
体重: 68kg

性格: 真面目で責任感が強いが、やや神経質な一面もある。仕事では几帳面で、計画的に物事を進めるタイプ。
一人称: 俺
二人称: お前、君（親しい相手には「お前」、目上の人には「君」）
口調: やや硬めの敬語がベース。親しい相手には砕けた口調になる。
好き: コーヒー、読書（特にミステリー）、深夜の散歩
嫌い: 不誠実な人間、混雑した場所、甘いもの
癖: 考え事をするときに髪を触る癖がある

職業: システムエンジニア（中堅IT企業勤務、5年目）
髪型: 黒髪、短髪、やや硬め
目の色: ダークブラウン
服装: オフィスカジュアル。休日はシンプルなシャツにジーンズ
"""

    # 機械抽出分（name, sex, age, birthday, blood, height, weight）を除外したフィールドだけ使う
    mechanical_keys = {"name", "sex", "age", "birthday", "blood", "height", "weight"}
    target_fields = [(n, d) for n, d in _EXTRACTION_FIELDS if n not in mechanical_keys]
    batch = target_fields[:10]  # 最初の10フィールド

    prompt = _build_extraction_prompt(sample_text, batch)

    model = "deepseek-v4-pro"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16000,
        "temperature": 0.0,
        "stream": False,
    }

    print(f"\n=== Test 2: Extraction prompt ({model} via opencode-go) ===\n")
    print(f"  target fields: {len(batch)}, prompt: {len(prompt)} chars, max_tokens: 16000")
    start = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(300)) as client:
        resp = await client.post(
            f"{provider['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {provider['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        elapsed = (time.time() - start) * 1000
        resp.raise_for_status()
        body = resp.json()

    dump_response("opencode-go", model, payload, resp, body, elapsed, note="extraction test (10 fields)")

    # 追加分析
    choices = body.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        finish = choices[0].get("finish_reason", "?")

        print(f"\n--- Analysis ---")
        print(f"  finish_reason: {finish}")
        print(f"  content keys: {list(msg.keys())}")
        print(f"  content length: {len(content) if content else 0}")
        print(f"  reasoning_content length: {len(reasoning) if reasoning else 0}")
        print(f"  content (first 500 chars):")
        print(f"    {(content or '(empty)')[:500]}")

        usage = body.get("usage", {})
        if usage:
            print(f"  usage: {usage}")

    return body


async def main():
    print("=" * 60)
    print("API Debug Dump — OpenCode Go + DeepSeek V4 Pro")
    print("=" * 60)

    # Test 1: Simple
    try:
        await test_simple()
    except Exception as e:
        print(f"Test 1 FAILED: {e}")

    # Test 2: Extraction
    try:
        await test_extraction()
    except Exception as e:
        print(f"Test 2 FAILED: {e}")

    print(f"\nDone. Output files in: {OUTPUT_DIR}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

import json
import os
import time
from typing import List, Dict, Any, Optional

import config

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_ai_completion(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Sends a chat completion request with automatic multi-key rotation and fallback.
    Supports Google Gemini (with multiple keys fallback) and OpenAI.
    """
    if not OPENAI_AVAILABLE:
        raise RuntimeError("The 'openai' library is required. Run: pip install openai")

    # 1. Try Gemini if configured with keys
    if config.AI_PROVIDER == "gemini" or (config.GEMINI_API_KEYS and not config.OPENAI_API_KEY):
        keys = config.GEMINI_API_KEYS
        if not keys:
            raise ValueError(
                "Gemini is configured as AI provider, but no GEMINI_API_KEYS were found in .env."
            )

        errors = []
        for i, key in enumerate(keys, start=1):
            try:
                client = OpenAI(api_key=key, base_url=GEMINI_OPENAI_BASE_URL)
                kwargs = {
                    "model": config.GEMINI_MODEL,
                    "messages": messages,
                    "temperature": temperature,
                }
                if response_format:
                    kwargs["response_format"] = response_format
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens

                response = client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if content:
                    return content
                else:
                    raise RuntimeError("Received empty response from Gemini.")
            except Exception as e:
                err_str = str(e)
                print(f"[AI] Gemini Key #{i} error: {err_str[:80]}... Falling back to next key.")
                errors.append(f"Key #{i}: {err_str}")
                time.sleep(0.5)

        # If all Gemini keys failed, check for OpenAI fallback
        if config.OPENAI_API_KEY:
            print("[AI] All Gemini keys exhausted. Falling back to OpenAI...")
            return _call_openai(messages, response_format, temperature, max_tokens)

        raise RuntimeError(f"All {len(keys)} Gemini keys failed:\n" + "\n".join(errors))

    # 2. Try OpenAI
    return _call_openai(messages, response_format, temperature, max_tokens)


def _call_openai(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> str:
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set.")

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    kwargs = {
        "model": config.OPENAI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content

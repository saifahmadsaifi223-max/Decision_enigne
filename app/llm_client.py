"""
Thin LLM client wrapper so agent code never talks to a specific provider's
SDK directly. Swap the implementation of `call_llm_json` to point at
whichever free/low-cost provider you land on (Groq, Google Gemini, a local
Ollama model, etc.) without touching any agent code.

Set the provider via the LLM_PROVIDER env var. Groq is wired up as the
default since it's free-tier, fast, and has an OpenAI-compatible API.
"""

import json
import os
import re
import time

from dotenv import load_dotenv
load_dotenv()  # safety net in case this module is imported without api.py
                # having run first (e.g. eval/evaluate.py run standalone)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
# llama-3.3-70b-versatile has been retired from Groq's lineup. Pick from
# your account's actual available models (Settings > Limits in the Groq
# console) -- openai/gpt-oss-120b is a solid default for this kind of
# structured JSON extraction/reasoning task.
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()


def call_llm_json(system: str, user: str, max_retries: int = 3) -> dict:
    """Calls the configured LLM with a system+user prompt and parses the
    response as JSON. Raises on repeated failure -- callers should decide
    how to degrade (e.g. treat as NEEDS_REVIEW rather than crash).
    """
    if LLM_PROVIDER == "groq":
        return _call_groq_json(system, user, max_retries)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")


def _call_groq_json(system: str, user: str, max_retries: int) -> dict:
    from groq import Groq  # pip install groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return json.loads(_strip_json_fences(content))
        except Exception as e:  # noqa: BLE001 -- deliberately broad, we retry then re-raise
            last_err = e
            err_text = str(e).lower()
            is_rate_limit = "rate_limit" in err_text or "429" in err_text or "rate limit" in err_text
            if attempt < max_retries:
                # Exponential backoff, longer for confirmed rate-limit errors
                # than for generic transient failures -- retrying instantly
                # after a 429 just hits the same limit again and wastes the
                # attempt. This matters a lot for a full evaluation run
                # (18 cases x ~8-12 calls each = 100+ sequential calls
                # against a free-tier TPM/RPM ceiling).
                delay = (10 if is_rate_limit else 2) * (2 ** attempt)
                time.sleep(delay)
    raise RuntimeError(f"LLM call failed after {max_retries + 1} attempts: {last_err}")




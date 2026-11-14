"""The model server: any OpenAI-compatible endpoint (vLLM on Colab or the workbench, Ollama).

    llm = chat_model(get_settings())         # callable: list of {"role", "content"} -> str
    python -m drug_safety.llm.client ping    # is MODEL_URL reachable, and is MODEL_NAME served?
    python -m drug_safety.llm.client hello   # one tiny completion, prints latency

The module only knows MODEL_URL, MODEL_NAME and MODEL_API_KEY (spec: the server can change
without code changes). Temperature is the spec's 0.1. MODEL_GUIDED_JSON=true asks vLLM to
constrain the output to the Report schema (spec: "if parse failures are frequent").
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable

import httpx

from drug_safety.config import Settings, get_settings

TEMPERATURE = 0.1
ChatFn = Callable[[list[dict]], str]


def chat_model(settings: Settings) -> ChatFn:
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        base_url=settings.model_url,
        api_key=settings.model_api_key,
        model=settings.model_name,
        temperature=TEMPERATURE,
        max_tokens=settings.model_max_tokens,
        timeout=settings.model_timeout_s,
        max_retries=1,  # network retry only; the validator retry is explain()'s job
    )
    if settings.model_guided_json:
        from drug_safety.llm.validator import Answer  # the Report plus verbatim quotes (v3)

        schema = {"name": "Report", "schema": Answer.model_json_schema(), "strict": True}
        llm = llm.bind(response_format={"type": "json_schema", "json_schema": schema})

    def call(messages: list[dict]) -> str:
        return llm.invoke(messages).content

    return call


def ping(settings: Settings) -> tuple[bool, str]:
    try:
        r = httpx.get(
            settings.model_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {settings.model_api_key}"},
            timeout=10,
        )
    except httpx.HTTPError as e:
        return False, f"{settings.model_url} unreachable: {e.__class__.__name__}"
    if r.status_code != 200:
        return False, f"{settings.model_url} answered HTTP {r.status_code}"
    served = [m["id"] for m in r.json().get("data", [])]
    if settings.model_name not in served:
        return False, f"{settings.model_name} not served; server has {served}"
    return True, f"{settings.model_name} served at {settings.model_url}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m drug_safety.llm.client")
    p.add_argument("cmd", choices=["ping", "hello"])
    args = p.parse_args(argv)
    s = get_settings()
    ok, detail = ping(s)
    print(("OK   " if ok else "FAIL ") + detail)
    if not ok or args.cmd == "ping":
        return 0 if ok else 1
    t = time.perf_counter()
    out = chat_model(s)([{"role": "user", "content": 'Reply with the JSON {"ok": true} only.'}])
    print(f"answer in {time.perf_counter() - t:.1f}s: {out[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

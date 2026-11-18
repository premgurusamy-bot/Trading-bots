"""AI providers: Claude (Anthropic) and ChatGPT (OpenAI).

Both run the same agent loop over the ERP tools in agents.py:
the model asks for tool calls, we execute them against the database, send results back,
and repeat until the model gives its final answer (or the step limit is reached).
"""
from __future__ import annotations

import base64
import json
import os

CLAUDE_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("AI_EFFORT", "medium")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol")
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
MAX_STEPS = int(os.environ.get("AI_MAX_STEPS", "10"))

PROVIDERS = ("claude", "openai")


def available() -> dict[str, bool]:
    return {"claude": bool(os.environ.get("ANTHROPIC_API_KEY")), "openai": bool(os.environ.get("OPENAI_API_KEY"))}


def resolve(preference: str | None) -> str | None:
    """Pick a provider: the preferred one if its key is set, otherwise whichever is configured."""
    av = available()
    pref = preference if preference in PROVIDERS else os.environ.get("AI_PROVIDER", "auto")
    if pref in PROVIDERS and av[pref]:
        return pref
    for p in PROVIDERS:
        if av[p]:
            return p
    return None


def model_name(provider: str) -> str:
    return CLAUDE_MODEL if provider == "claude" else OPENAI_MODEL


def run_loop(provider: str, system: str, messages: list[dict], tools: list[dict], execute) -> tuple[str, list[str], int, int]:
    """messages: [{"role": "user"|"assistant", "content": str}, ...]. execute(name, input) -> result."""
    if provider == "claude":
        return _claude_loop(system, messages, tools, execute)
    if provider == "openai":
        return _openai_loop(system, messages, tools, execute)
    raise ValueError(f"unknown provider {provider}")


def _call(execute, name: str, args: dict, actions: list[str]) -> tuple[str, bool]:
    try:
        out = execute(name, args)
        actions.append(f"{name}: {json.dumps(args, default=str)[:300]}")
        return json.dumps(out, default=str)[:20000], False
    except Exception as e:  # errors go back to the model so it can correct itself
        return f"Error: {e}", True


def _claude_loop(system, messages, tools, execute):
    import anthropic

    client = anthropic.Anthropic()
    msgs = list(messages)
    actions, tin, tout, summary = [], 0, 0, ""
    sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    for _ in range(MAX_STEPS):
        resp = client.beta.messages.create(
            model=CLAUDE_MODEL, max_tokens=16000, system=sys_blocks, tools=tools, messages=msgs,
            output_config={"effort": CLAUDE_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        tin += resp.usage.input_tokens or 0
        tout += resp.usage.output_tokens or 0
        if resp.stop_reason == "refusal":
            return "The model declined this request.", actions, tin, tout
        msgs.append({"role": "assistant", "content": resp.content})
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        summary = text or summary
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            return summary, actions, tin, tout
        results = []
        for c in calls:
            out, err = _call(execute, c.name, dict(c.input or {}), actions)
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": out, **({"is_error": True} if err else {})})
        msgs.append({"role": "user", "content": results})
    return (summary + "\n(stopped: step limit reached)").strip(), actions, tin, tout


def _openai_loop(system, messages, tools, execute):
    from openai import OpenAI

    client = OpenAI()
    oa_tools = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"], "strict": False} for t in tools]
    items: list = [{"role": m["role"], "content": m["content"]} for m in messages]
    actions, tin, tout, summary = [], 0, 0, ""
    for _ in range(MAX_STEPS):
        resp = client.responses.create(model=OPENAI_MODEL, instructions=system, input=items, tools=oa_tools)
        if resp.usage:
            tin += resp.usage.input_tokens or 0
            tout += resp.usage.output_tokens or 0
        items += resp.output
        summary = (resp.output_text or "").strip() or summary
        calls = [o for o in resp.output if getattr(o, "type", None) == "function_call"]
        if not calls:
            return summary, actions, tin, tout
        for c in calls:
            try:
                args = json.loads(c.arguments or "{}")
            except json.JSONDecodeError:
                items.append({"type": "function_call_output", "call_id": c.call_id, "output": "Error: arguments were not valid JSON"})
                continue
            out, _ = _call(execute, c.name, args, actions)
            items.append({"type": "function_call_output", "call_id": c.call_id, "output": out})
    return (summary + "\n(stopped: step limit reached)").strip(), actions, tin, tout


def generate_image(prompt: str, size: str = "1024x1024") -> bytes:
    """Create a PNG with OpenAI's image model (needs OPENAI_API_KEY)."""
    from openai import OpenAI

    client = OpenAI()
    r = client.images.generate(model=OPENAI_IMAGE_MODEL, prompt=prompt, size=size, n=1)
    return base64.b64decode(r.data[0].b64_json)

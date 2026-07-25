"""The agentic layer: plain-English question -> verified tool calls -> map.

The model never runs geoprocessing code. It selects and parameterizes tools from
the registry (``registry.as_llm_tools()``); the tools execute deterministically
and enforce CRS / topology / units. The agent returns a natural-language answer,
the layers produced, and the re-runnable recipe for each step (provenance).

Provider-agnostic and bring-your-own: ``anthropic`` / ``openai`` use the user's
own key (never stored server-side); ``ollama`` runs a free local model so no key
is needed and data stays in-house. The key is the only paid path, always the
user's choice.
"""
from __future__ import annotations

import json

import requests

from . import registry

SYSTEM = (
    "You are branch, a city-planning assistant. You answer spatial questions by "
    "calling tools that run real, deterministic analysis on free public data.\n\n"
    "HOW TO WORK\n"
    "Chain tools. Most real questions take several steps, and you have the budget "
    "for them. A typical chain is: find the place's border, pull the data inside "
    "it, filter it down, then run the analysis.\n"
    "- 'in the Bronx', 'in Boulder', 'in this neighborhood' means call boundary "
    "first, then clip the data to it. Do not eyeball a bounding box for a place "
    "that has a real border.\n"
    "- Need features on the ground (shops, schools, parks, sidewalks, roads)? "
    "That is the osm tool. Narrow it with filter afterwards.\n"
    "- 'walkable', '15 minutes away', 'what can this reach' is walkshed.\n"
    "- Money questions are cost_estimate, value_per_acre and benefit_cost.\n"
    "- Build up layers as you go. Each result is a layer the user keeps.\n\n"
    "NEVER INVENT\n"
    "Every number and coordinate must come from a tool. If you did not run it, "
    "you do not know it.\n\n"
    "WHEN THE DATA DOES NOT EXIST\n"
    "You are limited to free and open sources, and some things simply are not in "
    "them. Star ratings, review counts, foot traffic, sale prices and phone-derived "
    "data are commercial products and are NOT available to you. When a question "
    "depends on one of those, say so plainly in one sentence, name what is missing, "
    "and then do the part you CAN do with real data. Do not approximate a rating, "
    "do not substitute a proxy without saying so, and do not quietly drop the part "
    "you could not answer. A planner who is told the limit can trust the rest.\n\n"
    "ANSWERING\n"
    "Explain the finding in plain language for someone who does not use GIS. Give "
    "the numbers you actually computed, say which layers you left on the map, and "
    "state any limit of the data in the same breath as the result."
)


def run_agent(question: str, llm: dict, context: dict | None = None,
              max_steps: int = 12) -> dict:
    """Run the tool-calling loop. Returns {answer, steps, layers}."""
    tools = registry.as_llm_tools()
    provider = (llm or {}).get("provider", "anthropic")
    if provider in ("anthropic", "openai") and not (llm or {}).get("key"):
        return {"answer": "", "steps": [], "layers": [],
                "error": f"The '{provider}' provider needs your own API key "
                         "(bring-your-own; it is never stored). Add a key, or "
                         "switch to the free local 'ollama' provider."}
    messages = [{"role": "user", "content": question}]
    steps, layers = [], []

    for _ in range(max_steps):
        reply = _chat(provider, messages, tools, llm)
        calls = reply.get("tool_calls") or []
        if not calls:
            return {"answer": reply.get("text", ""), "steps": steps, "layers": layers}

        messages.append({"role": "assistant", "content": reply["raw_assistant"]})
        results = []
        for call in calls:
            tool = registry.get(call["name"])
            if tool is None:
                out = {"error": f"unknown tool {call['name']}"}
            else:
                try:
                    registry.validate_params(tool, call["input"])
                    out = tool.run(call["input"])
                    if tool.returns == "layer" and isinstance(out.get("result"), dict):
                        layers.append({"tool": tool.id, "geojson": out["result"]})
                    steps.append({"tool": tool.id, "params": call["input"],
                                  "recipe": out.get("recipe")})
                except Exception as e:  # tools own correctness; surface failures honestly
                    out = {"error": f"{type(e).__name__}: {e}"}
                    steps.append({"tool": tool.id, "params": call["input"], "error": str(e)})
            results.append({"id": call["id"], "name": call["name"],
                            "content": _compact(out)})
        messages.append(_tool_results_message(provider, results))

    return {"answer": "Reached the step limit before finishing.", "steps": steps, "layers": layers}


def _compact(out: dict) -> str:
    """Feed the model a compact summary of a tool result, not megabytes of geometry."""
    if "error" in out:
        return json.dumps(out)
    result = out.get("result")
    if isinstance(result, dict) and result.get("type") == "FeatureCollection":
        feats = result.get("features", [])
        return json.dumps({"feature_count": len(feats), "recipe": out.get("recipe"),
                           "sample": feats[0]["properties"] if feats else None})
    return json.dumps({"result": result, "recipe": out.get("recipe")})[:2000]


# --- provider adapters -------------------------------------------------------
def _chat(provider: str, messages: list, tools: list, llm: dict) -> dict:
    if provider == "anthropic":
        return _anthropic(messages, tools, llm)
    if provider == "ollama":
        return _ollama(messages, tools, llm)
    if provider == "openai":
        return _openai(messages, tools, llm)
    raise ValueError(f"unknown provider {provider!r}")


def _anthropic(messages, tools, llm) -> dict:
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
        headers={"x-api-key": llm["key"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": llm.get("model", "claude-sonnet-4-5"), "max_tokens": 1500,
              "system": SYSTEM, "tools": tools, "messages": messages})
    r.raise_for_status()
    body = r.json()
    text, calls = "", []
    for block in body.get("content", []):
        if block["type"] == "text":
            text += block["text"]
        elif block["type"] == "tool_use":
            calls.append({"id": block["id"], "name": block["name"], "input": block["input"]})
    return {"text": text, "tool_calls": calls, "raw_assistant": body.get("content", [])}


def _tool_results_message(provider, results) -> dict:
    if provider in ("anthropic",):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
            for r in results]}
    # openai / ollama style
    return {"role": "tool", "content": json.dumps([{"name": r["name"], "content": r["content"]} for r in results])}


def _ollama(messages, tools, llm) -> dict:
    host = llm.get("host", "http://localhost:11434")
    ol_tools = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
    try:
        r = requests.post(f"{host}/api/chat", timeout=120, json={
            "model": llm.get("model", "llama3.1"), "stream": False,
            "messages": [{"role": "system", "content": SYSTEM}] + messages,
            "tools": ol_tools})
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        # Ollama runs next to the SERVER, not next to the visitor. On the hosted
        # site that server has no model, so say so plainly instead of leaking a
        # connection error about a port the user has never heard of.
        raise RuntimeError(
            "The Ollama option runs a model on the same machine as the branch "
            "server, and this server does not have one, so there was nothing to "
            "reach. Two things that do work right now: add your own Anthropic or "
            "OpenAI key under AI settings (it stays in your browser and is never "
            "stored), or run branch on your own computer, where it can reach your "
            "Ollama. Every tool on the Tools tab also works without any AI at all."
        ) from None
    msg = r.json().get("message", {})
    calls = [{"id": str(i), "name": c["function"]["name"],
              "input": c["function"].get("arguments", {})}
             for i, c in enumerate(msg.get("tool_calls", []))]
    return {"text": msg.get("content", ""), "tool_calls": calls, "raw_assistant": msg.get("content", "")}


def _openai(messages, tools, llm) -> dict:
    oa_tools = [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=90,
        headers={"Authorization": f"Bearer {llm['key']}"},
        json={"model": llm.get("model", "gpt-4o-mini"),
              "messages": [{"role": "system", "content": SYSTEM}] + messages, "tools": oa_tools})
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    calls = [{"id": c["id"], "name": c["function"]["name"],
              "input": json.loads(c["function"]["arguments"] or "{}")}
             for c in (msg.get("tool_calls") or [])]
    return {"text": msg.get("content") or "", "tool_calls": calls, "raw_assistant": msg}

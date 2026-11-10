"""SLD reading agent: sends images + text to Claude and returns structured JSON."""
from __future__ import annotations

from typing import Any

import anthropic

from .prompts import SLD_SYSTEM_PROMPT, user_prompt
from .schema import SLD_TOOL
from .sld_loader import SLDPayload, build_image_set, encode


def extract_sld(payload: SLDPayload, api_key: str, model: str,
                detail: str = "High", extra_context: str = "") -> dict[str, Any]:
    images = build_image_set(payload, detail)
    if not images and not payload.text:
        raise ValueError("Nothing to analyse: the file produced no image and no text.")

    content: list[dict[str, Any]] = []
    for label, img in images:
        media_type, b64 = encode(img)
        content.append({"type": "text", "text": f"Image: {label}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    content.append({"type": "text", "text": user_prompt(extra_context, payload.text, [l for l, _ in images])})

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SLD_SYSTEM_PROMPT,
        tools=[SLD_TOOL],
        tool_choice={"type": "tool", "name": "record_sld"},
        messages=[{"role": "user", "content": content}],
    )

    result = next((b.input for b in resp.content if b.type == "tool_use"), None)
    if result is None:
        raise RuntimeError("The model did not return a structured SLD record.")
    if resp.stop_reason == "max_tokens":
        result.setdefault("observations", []).append(
            "WARNING: output was truncated (very large SLD). Some equipment may be missing - "
            "re-run per sheet or use 'Standard' detail.")

    result["_meta"] = {
        "model": model,
        "images_sent": len(images),
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "loader_notes": payload.notes,
    }
    return normalise(result)


def normalise(d: dict[str, Any]) -> dict[str, Any]:
    """Make sure all expected keys exist so the UI and checks never KeyError."""
    d.setdefault("project", {})
    for k in ("buses", "equipment", "unreadable_items", "missing_inputs", "observations"):
        d[k] = d.get(k) or []
    d.setdefault("summary", "")
    for eq in d["equipment"]:
        eq["ansi_functions"] = eq.get("ansi_functions") or []
        # quantity is deliberately left as-is (None = not stated); checks flag it.
    return d

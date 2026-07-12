from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_entries() -> tuple[dict, ...]:
    path = Path(__file__).with_name("meme_glossary.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(data["entries"])


def build_meme_context(text: str) -> str:
    normalized = str(text or "").casefold()
    if not normalized:
        return ""

    notes = [
        entry["note"]
        for entry in _load_entries()
        if any(str(trigger).casefold() in normalized for trigger in entry["triggers"])
    ]
    if not notes:
        return ""

    joined = "\n".join(f"- {note}" for note in notes)
    return (
        "[小散的网络梗备忘]\n"
        f"{joined}\n"
        "这些只是理解当前语境的背景。自然接梗即可；除非对方询问出处，"
        "不要主动做百科式解释，也不要为了展示懂梗而硬塞原句。"
    )

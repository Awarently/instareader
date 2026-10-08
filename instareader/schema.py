"""JSON schema of the result returned by POST /read and cached in SQLite."""

_STR_LIST = {"type": "array", "items": {"type": "string"}}

RESULT_SCHEMA = {
    "type": "object",
    "required": [
        "title_guess", "creator", "summary", "claims", "tools_or_products_mentioned",
        "links_mentioned", "action_items", "full_transcript", "onscreen_text",
        "visual_notes", "source", "processed_at", "model",
    ],
    "properties": {
        "title_guess": {"type": "string"},
        "creator": {"type": "string"},
        "summary": {**_STR_LIST, "maxItems": 5},
        "claims": _STR_LIST,
        "tools_or_products_mentioned": _STR_LIST,
        "links_mentioned": _STR_LIST,
        "action_items": _STR_LIST,
        "full_transcript": {"type": "string"},
        "onscreen_text": {"type": "string"},
        "visual_notes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["t", "onscreen_text", "visual"],
                "properties": {
                    "t": {"type": ["number", "null"]},
                    "onscreen_text": {"type": "string"},
                    "visual": {"type": "string"},
                },
            },
        },
        "source": {
            "type": "object",
            "required": ["url", "platform", "caption", "posted", "duration", "downloader", "language"],
            "properties": {
                "url": {"type": "string"},
                "platform": {"type": "string", "enum": ["instagram", "tiktok", "youtube", "x"]},
                "caption": {"type": "string"},
                "posted": {"type": ["string", "null"]},
                "duration": {"type": ["number", "null"]},
                "downloader": {"type": "string"},
                "language": {"type": ["string", "null"]},
            },
        },
        "processed_at": {"type": "string"},
        "model": {"type": "string"},
        "cached": {"type": "boolean"},
    },
}

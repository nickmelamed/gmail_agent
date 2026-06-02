TOOLS = [
    {
        "name": "triage_email",
        "description": "Score, categorize, and draft a reply for an email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "importance": {"type": "integer", "description": "0-100 importance score"},
                "category": {"type": "string", "description": "e.g. work, personal, finance, newsletter"},
                "reply_subject": {"type": "string"},
                "reply_body": {"type": "string", "description": "Draft reply following user profile rules"},
                "skip_reason": {"type": "string", "description": "If no reply needed, explain why"},
            },
            "required": ["importance", "category"],
        },
    },
    {
        "name": "request_clarification",
        "description": "Use when the email has an unclear deadline or ask. Drafts a reply asking for clarification.",
        "input_schema": {
            "type": "object",
            "properties": {
                "importance": {"type": "integer"},
                "question": {"type": "string", "description": "The clarifying question to ask"},
            },
            "required": ["importance", "question"],
        },
    },
    {
        "name": "escalate",
        "description": "Flag an email for human review — use for contracts, financial commitments, or sensitive situations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "importance": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["importance", "reason"],
        },
    },
]

"""Closed JSON schemas for the established menu/submenu controller."""

def menu_schema(letters: list[str]) -> dict:
    return {"type": "object", "properties": {
        "thinking": {"type": "string"}, "choice": {"type": "string", "enum": list(letters)}},
        "required": ["thinking", "choice"], "additionalProperties": False}

def submenu_schema(letters: list[str]) -> dict:
    return {"type": "object", "properties": {"thinking": {"type": "string"},
        "choice": {"type": "string", "enum": list(letters)}},
        "required": ["thinking", "choice"], "additionalProperties": False}

def activity_schema(letters: list[str]) -> dict:
    return {"type": "object", "properties": {
        "thinking": {"type": "string"},
        "choice": {"type": "string", "enum": [""] + list(letters)},
        "done": {"type": "string", "enum": ["", "completed", "could_not_complete"]},
        "answer": {"type": "string"}},
        "required": ["thinking", "choice", "done", "answer"],
        "additionalProperties": False}

def completion_schema() -> dict:
    return {"type": "object", "properties": {
        "thinking": {"type": "string"},
        "done": {"type": "string", "enum": ["completed", "could_not_complete"]},
        "answer": {"type": "string"}},
        "required": ["thinking", "done", "answer"], "additionalProperties": False}

def freetext_schema(field: str) -> dict:
    return {"type": "object", "properties": {"thinking": {"type": "string"},
        field: {"type": "string"}}, "required": ["thinking", field],
        "additionalProperties": False}

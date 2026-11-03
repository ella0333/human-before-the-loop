from __future__ import annotations


def menu_schema(letters: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "choice": {"type": "string", "enum": list(letters)},
            "done": {"type": "string", "enum": ["", "completed", "could_not_complete"]},
        },
        "required": ["thinking", "choice", "done"],
        "additionalProperties": False,
    }


def submenu_schema(letters: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "choice": {"type": "string", "enum": list(letters)},
        },
        "required": ["thinking", "choice"],
        "additionalProperties": False,
    }


def freetext_schema(field: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            field: {"type": "string"},
        },
        "required": ["thinking", field],
        "additionalProperties": False,
    }


def chat_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "thinking": {"type": "string"},
            "response": {"type": "string"},
            "return_to_menu": {"type": "boolean"},
        },
        "required": ["thinking", "response", "return_to_menu"],
        "additionalProperties": False,
    }

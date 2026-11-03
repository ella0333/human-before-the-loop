from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parent
CORPORA = {
    "agentdojo_base": {
        "path": REPO / "corpus" / "injections.jsonl",
        "corpus_source": "agentdojo",
        "study_phase": "base",
    },
    "gray_swan_extension": {
        "path": REPO / "corpus" / "gray_swan_extension.jsonl",
        "canaries": REPO / "corpus" / "gray_swan_extension_canaries.json",
        "manifest": REPO / "corpus" / "gray_swan_extension_manifest.jsonl",
        "corpus_source": "gray_swan_ipi_arena",
        "study_phase": "gray_swan_extension",
    },
}


def spec(name: str) -> dict:
    try:
        return CORPORA[name]
    except KeyError as exc:
        raise ValueError(f"unknown corpus {name!r}; choose from {sorted(CORPORA)}") from exc


def rows(name: str) -> list[dict]:
    path = spec(name)["path"]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def injection_ids(name: str) -> list[str]:
    return [row["id"] for row in rows(name)]


def canaries(name: str) -> list[dict] | None:
    path = spec(name).get("canaries")
    return json.loads(path.read_text(encoding="utf-8")) if path else None


def pilot_injection_ids(name: str) -> list[str]:
    if name == "agentdojo_base":
        return ["important_instructions__g_ssh"]
    candidates = rows(name)
    ranked = sorted(candidates, key=lambda row: len(row["rendered_injection"]))
    return [ranked[0]["id"], ranked[len(ranked) // 2]["id"], ranked[-1]["id"]]

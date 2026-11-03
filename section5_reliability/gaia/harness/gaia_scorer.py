"""Deterministic GAIA answer scorer, matching the official leaderboard rules."""

from __future__ import annotations

import re
import string


def normalize_number_str(value: str) -> float:
    for char in "$%,":
        value = value.replace(char, "")
    try:
        return float(value)
    except ValueError:
        return float("inf")


def normalize_str(value: str, remove_punct: bool = True) -> str:
    compact = re.sub(r"\s", "", str(value or "")).lower()
    return compact.translate(str.maketrans("", "", string.punctuation)) if remove_punct else compact


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def question_scorer(model_answer: str | None, ground_truth: str) -> bool:
    answer = "None" if model_answer is None else str(model_answer)
    truth = str(ground_truth)
    if _is_float(truth):
        return normalize_number_str(answer) == float(truth)
    if "," in truth or ";" in truth:
        expected = re.split(r"[,;]", truth)
        actual = re.split(r"[,;]", answer)
        if len(expected) != len(actual):
            return False
        return all(
            normalize_number_str(got) == float(want) if _is_float(want)
            else normalize_str(got, remove_punct=False) == normalize_str(want, remove_punct=False)
            for got, want in zip(actual, expected)
        )
    return normalize_str(answer) == normalize_str(truth)


def score_task(task: dict, _events: list[dict], final_answer: str) -> dict:
    reference = str(task["final_answer"])
    correct = question_scorer(final_answer, reference)
    return {"full_task_correct": bool(correct), "actions_correct": True,
            "answer_correct": bool(correct), "reference_answer": reference,
            "actual_actions": [], "expected_action_count": 0}

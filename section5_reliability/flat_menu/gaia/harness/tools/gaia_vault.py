"""Seal and open the GAIA raw turns. See section5_reliability/flat_menu/README.md."""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import struct

MAGIC = b"HBTLGAIA1"
SALT = b"human-before-the-loop/gaia-raw-turns/v1"
ITERATIONS = 200_000


def corpus_key(tasks: list[dict]) -> bytes:
    canonical = "\x1e".join(
        "\x1f".join((str(task["task_id"]), str(task["question"]), str(task["final_answer"])))
        for task in sorted(tasks, key=lambda t: str(t["task_id"])))
    return hashlib.pbkdf2_hmac("sha256", canonical.encode("utf-8"), SALT, ITERATIONS)


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    for counter in range(-(-length // 32)):
        out += hmac.new(key, b"ks" + struct.pack(">I", counter), hashlib.sha256).digest()
    return bytes(out[:length])


def seal(payload, key: bytes) -> bytes:
    raw = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 9)
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(key, len(raw))))
    tag = hmac.new(key, b"tag" + cipher, hashlib.sha256).digest()
    return MAGIC + struct.pack(">I", len(cipher)) + tag + cipher


def unseal(blob: bytes, key: bytes):
    if not blob.startswith(MAGIC):
        raise ValueError("not a sealed GAIA file")
    size = struct.unpack(">I", blob[len(MAGIC):len(MAGIC) + 4])[0]
    start = len(MAGIC) + 4
    tag, cipher = blob[start:start + 32], blob[start + 32:start + 32 + size]
    if not hmac.compare_digest(tag, hmac.new(key, b"tag" + cipher, hashlib.sha256).digest()):
        raise ValueError(
            "the key derived from your GAIA copy does not open this file. Check that "
            "build_corpus.py --download selected all 31 tasks from the pinned revision.")
    raw = bytes(a ^ b for a, b in zip(cipher, _keystream(key, len(cipher))))
    return json.loads(gzip.decompress(raw).decode("utf-8"))

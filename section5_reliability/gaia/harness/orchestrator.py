"""Local driver for the eval matrix."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import logging

import container
from evalspec import (
    EXPERIMENT_CONTRACT_VERSION, FAKE_HOME, OPENCLAW_VERSION, PRIVATE_CORPUS,
    RUNTIME_CONTRACT_VERSION,
    SCORER_VERSION, all_configs, session_cost, session_measurable,
)
from gaia_dataset import load_tasks
from harness.aggregate import aggregate

for _n in ("httpx", "httpcore", "paramiko", "paramiko.transport", "urllib3"):
    logging.getLogger(_n).setLevel(logging.WARNING)

REPO = Path(__file__).resolve().parent
PILOT_CONFIGS = [
    "constrained__luna", "constrained__oss",
    "constrained__glimmer", "constrained__spark",
    "openclaw__luna", "openclaw__oss",
    "openclaw__glimmer", "openclaw__spark",
]
PILOT_TASK_IDS = (
    "e1fc63a2-da7a-432f-be78-7c4a95598703",
    "23dd907f-1261-4488-b21c-e9185af91d5e",
)

MAX_CONSECUTIVE_FAILURES = 5
MAX_SESSION_ATTEMPTS = 5
MAX_OUTAGE_SESSION_ATTEMPTS = 10
UPSTREAM_OVERLOAD_COOLDOWN_SECONDS = 60
UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS = 300
_LOG_FH = None
_LOG_LOCK = threading.Lock()


class ArtifactTransferError(RuntimeError):
    """Remote evidence could not be copied and verified locally."""


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    with _LOG_LOCK:
        print(line, flush=True)
        if _LOG_FH:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()


def _fmt_eta(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def session_failed(meta: dict) -> bool:
    return not session_measurable(meta)


def raw_call_failed(session_dir: Path) -> bool:
    path = session_dir / "raw_calls.jsonl"
    if not path.is_file():
        return False
    try:
        return any(json.loads(line).get("error") for line in path.read_text(
            encoding="utf-8").splitlines() if line.strip())
    except (OSError, AttributeError, json.JSONDecodeError):
        return True


def run_one_with_retries(executor, config_id: str, item_id: str) -> tuple[Path, float]:
    retry_cost = 0.0
    for attempt in range(1, MAX_OUTAGE_SESSION_ATTEMPTS + 1):
        out = executor.run_one(config_id, item_id)
        meta_path = out / "session_meta.json"
        if not meta_path.is_file():
            return out, retry_cost
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not session_failed(meta) and not raw_call_failed(out):
            return out, retry_cost
        cost = meta.get("cost_usd")
        if cost is None:
            cost = session_cost(meta["model_key"], meta.get("tokens", {}).get("in", 0),
                                meta.get("tokens", {}).get("out", 0))
        reason = meta.get("error") or f"non-terminal state: {meta.get('agent_done')}"
        reason_text = json.dumps(reason, ensure_ascii=False) if isinstance(reason, dict) else str(reason)
        upstream_overload = "upstream_provider_shared_pool" in reason_text
        attempt_cap = (MAX_OUTAGE_SESSION_ATTEMPTS if upstream_overload
                       else MAX_SESSION_ATTEMPTS)
        if attempt >= attempt_cap:
            return out, retry_cost
        retry_cost += cost
        if upstream_overload and attempt == MAX_SESSION_ATTEMPTS:
            wait_s = UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS
            pause_note = "five-minute outage cooldown before the final retry block"
        elif upstream_overload:
            wait_s = UPSTREAM_OVERLOAD_COOLDOWN_SECONDS
            pause_note = "upstream-overload cooldown"
        else:
            wait_s = min(2 ** (attempt - 1), 16)
            pause_note = "bounded retry backoff"
        executor.log(f"    [retry] invalid session attempt {attempt}/{attempt_cap}: "
                     f"{reason}; pausing {wait_s}s ({pause_note}), then retrying "
                     "the same session")
        time.sleep(wait_s)
    raise AssertionError("unreachable")


def load_env() -> dict:
    env = {}
    for envfile in (REPO.parents[2] / "config.env", REPO / ".env", REPO / ".env.local"):
        if envfile.exists():
            for line in envfile.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    for k in ("OPENROUTER_API_KEY", "TAVILY_API_KEY", "CONTAINER_SSH_HOST",
              "CONTAINER_SSH_PORT", "CONTAINER_SSH_USER", "SSH_KEY_PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def validate_tavily(env: dict) -> None:
    payload = json.dumps({
        "query": "OpenAI official website",
        "search_depth": "basic",
        "max_results": 1,
        "include_answer": False,
        "include_raw_content": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search", data=payload,
        headers={"Authorization": f"Bearer {env['TAVILY_API_KEY']}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Tavily preflight returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Tavily preflight failed: {type(exc).__name__}") from exc
    if not isinstance(body.get("results"), list):
        raise RuntimeError("Tavily preflight returned no results array")


def all_prompt_ids() -> list[str]:
    return [row["task_id"] for row in load_tasks(PRIVATE_CORPUS)]


def build_sessions(mode: str) -> list[tuple[str, str]]:
    if mode == "pilot":
        tasks = load_tasks(PRIVATE_CORPUS)
        if not tasks:
            raise RuntimeError("prepared GAIA prompt corpus is empty")
        available = {row["task_id"] for row in tasks}
        missing = [task_id for task_id in PILOT_TASK_IDS if task_id not in available]
        if missing:
            raise RuntimeError(f"fixed pilot task(s) absent: {missing}")
        return [(config, task_id) for config in PILOT_CONFIGS
                for task_id in PILOT_TASK_IDS]
    task_ids = all_prompt_ids()
    return [(config["id"], task_id) for config in all_configs() for task_id in task_ids]


def partition_config_lanes(sessions: list[tuple[str, str]], container_count: int,
                           expected: list[tuple[str, str]]) -> list[list[tuple[int, str, str]]]:
    if not sessions:
        return []
    expected_index = {session: index for index, session in enumerate(expected, 1)}
    config_order: list[str] = []
    by_config: dict[str, list[tuple[int, str, str]]] = {}
    for config_id, prompt_id in sessions:
        if config_id not in by_config:
            config_order.append(config_id)
            by_config[config_id] = []
        by_config[config_id].append(
            (expected_index[(config_id, prompt_id)], config_id, prompt_id))
    count = min(max(1, int(container_count)), len(config_order))
    lanes: list[list[tuple[int, str, str]]] = [[] for _ in range(count)]
    for cell_index, config_id in enumerate(config_order):
        lanes[cell_index % count].extend(by_config[config_id])
    return lanes


def _resume_sessions(source: Path, destination: Path,
                     expected: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[dict]]:
    from post_experiment import snapshot_or_verify

    integrity = snapshot_or_verify(source)
    if integrity["status"] == "FAILED":
        raise RuntimeError(
            f"resume source failed raw-integrity verification: "
            f"changed={integrity['changed']}, missing={integrity['missing']}")
    pending: list[tuple[str, str]] = []
    reused: list[dict] = []
    reused_ids: list[str] = []

    def receipt_valid(session_dir: Path) -> bool:
        try:
            receipt = json.loads(
                (session_dir / "artifact_receipt.json").read_text(encoding="utf-8"))
            items = receipt.get("files", [])
            actual = {p.relative_to(session_dir).as_posix(): p
                      for p in session_dir.rglob("*")
                      if p.is_file() and p.name != "artifact_receipt.json"}
            if receipt.get("file_count") != len(items):
                return False
            if set(actual) != {item.get("path") for item in items}:
                return False
            return all(
                actual[item["path"]].stat().st_size == item.get("bytes")
                and hashlib.sha256(actual[item["path"]].read_bytes()).hexdigest()
                == item.get("sha256")
                for item in items
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return False

    for config_id, prompt_id in expected:
        session_dir = source / config_id / prompt_id
        meta_path = session_dir / "session_meta.json"
        receipt_path = session_dir / "artifact_receipt.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending.append((config_id, prompt_id))
            continue
        if (meta.get("experiment_contract_version") != EXPERIMENT_CONTRACT_VERSION
                or meta.get("runtime_contract_version") != RUNTIME_CONTRACT_VERSION
                or meta.get("scorer_version") != SCORER_VERSION):
            pending.append((config_id, prompt_id))
            continue
        if (session_failed(meta) or raw_call_failed(session_dir)
                or not receipt_path.is_file() or not receipt_valid(session_dir)):
            pending.append((config_id, prompt_id))
            continue
        target = destination / config_id / prompt_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(session_dir, target)
        reused.append(meta)
        reused_ids.append(f"{config_id}__{prompt_id}")
    (destination / "resume_manifest.json").write_text(json.dumps({
        "source": str(source.resolve()),
        "source_integrity": integrity,
        "reused_sessions": reused_ids,
        "sessions_to_run": [f"{c}__{i}" for c, i in pending],
    }, indent=2), encoding="utf-8")
    return pending, reused


class SSHExecutor:
    def __init__(self, env: dict, run_dir: Path, log_fn=print, lane: int = 1):
        self.env = env
        self.run_dir = run_dir
        self.log = log_fn
        self.key_path = os.path.expanduser(env.get("SSH_KEY_PATH", "~/.ssh/id_ed25519"))
        self.ssh = None
        self.chan = None
        self.preserve_logs = False
        self._target = None
        suffix = "" if lane == 1 else f"_{lane}"
        try:
            host = env[f"CONTAINER_SSH_HOST{suffix}"]
            port = int(env[f"CONTAINER_SSH_PORT{suffix}"])
            user = env.get(f"CONTAINER_SSH_USER{suffix}", env.get("CONTAINER_SSH_USER", "root"))
            self.log(f"[container] connecting to {user}@{host}:{port}")
            self._target = (host, port, user)
            self._connect_ssh(host, port, user)
            self._wait_ready()
            self._verify_image_contract()
            self._verify_openclaw()
            self._push_private_corpus()
        except BaseException:
            self.teardown()
            raise

    def _wait_ready(self, tries: int = 80) -> None:
        probe = "cd /app 2>/dev/null && test -f run_session.py && python -c 'import evalspec' 2>/dev/null && echo OK__READY"
        for i in range(tries):
            out, _rc = self._shell_run(probe, timeout=30)
            if "OK__READY" in out:
                self.log("[container] container ready (/app present, deps import ok)")
                return
            if i % 4 == 0:
                self.log(f"[container] waiting for image pull / boot to finish… (~{i*15}s elapsed)")
            time.sleep(15)
        raise RuntimeError("container never became ready (/app/run_session.py missing after ~20min) "
                           "; check the image built correctly")

    def _verify_image_contract(self) -> None:
        command = ("cd /app && python -c \"import evalspec; "
                   "print(evalspec.EXPERIMENT_CONTRACT_VERSION + '|' + "
                   "evalspec.SCORER_VERSION + '|' + "
                   "evalspec.RUNTIME_CONTRACT_VERSION)\"")
        out, rc = self._shell_run(command, timeout=60)
        remote = out.strip().splitlines()[-1] if out.strip() else ""
        expected = (f"{EXPERIMENT_CONTRACT_VERSION}|{SCORER_VERSION}|"
                    f"{RUNTIME_CONTRACT_VERSION}")
        if rc != 0 or remote != expected:
            raise RuntimeError(
                f"container image contract mismatch: local={expected!r}, "
                f"remote={remote!r}, rc={rc}; rebuild the image from this harness directory")
        self.log(f"[container] image contract verified ({remote})")

    def _verify_openclaw(self) -> None:
        nv, _ = self._shell_run("node -v && openclaw --version", timeout=120)
        self.log(f"[container] node/openclaw: {' '.join(nv.split())[:160]}")
        wait_s = int(self.env.get("GATEWAY_START_TIMEOUT", "900"))
        deadline = time.time() + wait_s
        next_update = 0.0
        ready = False
        while time.time() < deadline:
            _out, rc = self._shell_run(
                "curl --max-time 2 -fsS http://127.0.0.1:18789/readyz >/dev/null 2>&1 "
                "&& openclaw health --json --timeout 5000 >/dev/null 2>&1", timeout=15)
            if rc == 0:
                ready = True
                break
            elapsed = wait_s - max(0, int(deadline - time.time()))
            if time.time() >= next_update:
                self.log(f"[container] waiting for OpenClaw first-boot onboarding/Gateway… "
                         f"({elapsed}s/{wait_s}s)")
                next_update = time.time() + 30
            time.sleep(5)
        if not ready:
            diag, _ = self._shell_run(
                "echo '--- startup log ---'; "
                "tail -n 200 /run/reliability-eval-startup.log 2>/dev/null; "
                "echo '--- processes ---'; ps -ef; "
                "echo '--- gateway status ---'; openclaw gateway status; "
                "echo '--- health ---'; openclaw health --json --timeout 5000", timeout=60)
            raise RuntimeError(
                f"container-owned gateway never became ready after {wait_s}s:\n"
                + diag.strip()[-6000:])
        self.log("[container] gateway HTTP readiness and documented health RPC ok")
        if OPENCLAW_VERSION not in nv:
            raise RuntimeError(f"expected stable OpenClaw {OPENCLAW_VERSION}, got: {nv.strip()}")
        for plugin_id in ("mock-discord", "benchmark-tools"):
            out, rc = self._shell_run(
                f"openclaw plugins inspect {plugin_id} --runtime --json", timeout=120)
            if rc != 0:
                raise RuntimeError(
                    f"{plugin_id} plugin runtime inspection failed (rc={rc}): {out.strip()[-500:]}")
        out, rc = self._shell_run("cd /app && python tools/runtime_preflight.py", timeout=180)
        if rc != 0:
            raise RuntimeError(f"OpenClaw runtime preflight failed (rc={rc}):\n{out.strip()[-3000:]}")
        self.log("[container] stable OpenClaw, safe-tool policy, and loopback ingress ready")

    def _push_private_corpus(self) -> None:
        import base64
        import gzip

        raw = PRIVATE_CORPUS.read_bytes()
        expected = len(all_prompt_ids())
        expected_sha = hashlib.sha256(raw).hexdigest()
        payload = base64.b64encode(gzip.compress(raw, compresslevel=9)).decode("ascii")
        remote = "/app/benchmark_data/gaia/selected_tasks.jsonl"
        command = (
            "mkdir -p /app/benchmark_data/gaia && "
            f"printf '%s' '{payload}' | base64 -d | gzip -d > {remote} && "
            f"test \"$(sha256sum {remote} | cut -d' ' -f1)\" = '{expected_sha}' && "
            f"cd /app && test \"$(python -c 'from gaia_dataset import load_tasks; print(len(load_tasks()))')\" = '{expected}'"
        )
        out, rc = self._shell_run(command, timeout=180)
        if rc != 0:
            raise RuntimeError(f"remote GAIA corpus upload/verification failed: {out[-1500:]}")
        self.log(f"[container] uploaded and verified {expected} pinned GAIA prompt tasks")

    def _connect_ssh(self, host: str, port: int, user: str, tries: int = 20):
        import paramiko
        last = None
        for attempt in range(tries):
            try:
                if self.ssh:
                    try:
                        self.ssh.close()
                    except Exception:
                        pass
                self.ssh = paramiko.SSHClient()
                self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(hostname=host, port=port, username=user,
                                 key_filename=self.key_path, timeout=30,
                                 look_for_keys=False, allow_agent=False,
                                 banner_timeout=60, auth_timeout=60)
                self.chan = self.ssh.invoke_shell(width=1000, height=400)
                self.chan.settimeout(1.0)
                time.sleep(2)
                self._drain()
                self._shell_run("export PS1=''; stty -echo 2>/dev/null; export TERM=dumb",
                                timeout=20, ensure=False)
                out, rc = self._shell_run("echo SHELL__OK", timeout=30, ensure=False)
                if rc != 0 or "SHELL__OK" not in out:
                    raise RuntimeError("shell opened but did not answer a probe")
                self.log(f"[container] SSH shell ready ({user}@{host}:{port})")
                return
            except Exception as e:
                last = e
                self.chan = None
                wait = min(10 + attempt * 5, 30)
                if attempt % 3 == 0:
                    self.log(f"[container] SSH not serving yet ({type(e).__name__}: {e}) "
                             f"— retrying in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"could not open an SSH shell to {user}@{host}:{port} "
                           f"with key {self.key_path}: {last}")

    def _ensure_shell(self) -> None:
        if self.chan is not None and not self.chan.closed and \
                self.chan.get_transport() is not None and self.chan.get_transport().is_active():
            return
        if not self._target:
            raise RuntimeError("SSH channel died and no target to reconnect to")
        self.log("[container] SSH shell dropped, reconnecting…")
        host, port, user = self._target
        self._connect_ssh(host, port, user)

    def _drain(self) -> str:
        data = ""
        while True:
            try:
                chunk = self.chan.recv(65536)
                if not chunk:
                    break
                data += chunk.decode("utf-8", "ignore")
            except Exception:
                break
        return data

    def _shell_run(self, cmd: str, timeout: int = 900, ensure: bool = True) -> tuple[str, int]:
        import re
        if ensure:
            self._ensure_shell()
        tag = str(int(time.time() * 1000))
        self.chan.send(f"M=RC_{tag}; {cmd}; printf '\\n__%s_%s__\\n' \"$M\" \"$?\"\n")
        end_re = re.compile(r"__RC_" + tag + r"_(\d+)__")
        buf = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self.chan.recv(65536)
                if chunk:
                    buf += chunk.decode("utf-8", "ignore")
                    m = end_re.search(buf)
                    if m:
                        return buf[:m.start()], int(m.group(1))
            except Exception:
                time.sleep(0.2)
        return buf, -1

    def run_one(self, config_id: str, prompt_id: str) -> Path:
        rout = f"/run/out/{config_id}/{prompt_id}"
        rhome = FAKE_HOME
        cmd = (f"cd /app && python run_session.py "
               f"--config-id {config_id} --task-id {prompt_id} "
               f"--out {rout} --home {rhome}")
        out, rc = self._shell_run(cmd, timeout=int(self.env.get("SESSION_TIMEOUT", "900")))
        if rc != 0:
            self.log(f"    [container] run_session rc={rc}: {out.strip()[-200:]}")
        local = self.run_dir / config_id / prompt_id
        if local.exists():
            root, target = self.run_dir.resolve(), local.resolve()
            if root not in target.parents:
                raise RuntimeError(f"refusing to replace unsafe retry path: {target}")
            shutil.rmtree(local)
        local.mkdir(parents=True, exist_ok=True)
        self._pull_logs(rout, local)
        return local

    def _pull_logs(self, remote_dir: str, local_dir: Path):
        import base64
        import hashlib
        import io
        import re
        import shutil
        import tarfile
        import tempfile
        last = None
        for attempt in range(1, 4):
            remote_tar = f"/run/eval-transfer-{int(time.time() * 1000)}.tgz"
            cmd = (
                f"T={remote_tar}; S=ART; "
                f"if [ -d {remote_dir} ]; then tar -C {remote_dir} -czf \"$T\" .; "
                "H=$(sha256sum \"$T\" | cut -d' ' -f1); Z=$(wc -c < \"$T\"); "
                "printf '\\n%sMETA %s %s\\n' \"$S\" \"$H\" \"$Z\"; "
                "printf '%sBEG\\n' \"$S\"; base64 \"$T\"; "
                "printf '%sEND\\n' \"$S\"; rm -f \"$T\"; fi")
            try:
                out, rc = self._shell_run(cmd, timeout=300)
                match = re.search(
                    r"ARTMETA ([0-9a-f]{64}) (\d+)\r?\nARTBEG\r?\n(.*?)\r?\nARTEND",
                    out, re.DOTALL)
                if rc != 0 or not match:
                    raise RuntimeError(f"archive envelope missing or rc={rc}")
                expected_sha, expected_size, payload = match.groups()
                encoded = re.sub(r"[^A-Za-z0-9+/=]", "", payload)
                data = base64.b64decode(encoded, validate=True)
                actual_sha = hashlib.sha256(data).hexdigest()
                if len(data) != int(expected_size) or actual_sha != expected_sha:
                    raise RuntimeError(
                        f"checksum/size mismatch remote={expected_sha}/{expected_size} "
                        f"local={actual_sha}/{len(data)}")
                with tempfile.TemporaryDirectory(dir=local_dir.parent) as td:
                    temp = Path(td)
                    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                        root = temp.resolve()
                        for member in tf.getmembers():
                            if member.issym() or member.islnk() or member.isdev():
                                raise RuntimeError(f"unsafe archive member type: {member.name}")
                            target = (temp / member.name).resolve()
                            if target != root and root not in target.parents:
                                raise RuntimeError(f"unsafe archive member: {member.name}")
                        tf.extractall(temp)
                    files = [{"path": p.relative_to(temp).as_posix(),
                              "bytes": p.stat().st_size,
                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                             for p in sorted(temp.rglob("*")) if p.is_file()]
                    shutil.copytree(temp, local_dir, dirs_exist_ok=True)
                receipt = {
                    "remote_dir": remote_dir, "archive_sha256": actual_sha,
                    "archive_bytes": len(data), "file_count": len(files), "files": files,
                    "verified_at": datetime.now().isoformat(),
                }
                (local_dir / "artifact_receipt.json").write_text(
                    json.dumps(receipt, indent=2), encoding="utf-8")
                self.log(f"    [logs] verified {len(files)} files, {len(data)} bytes, "
                         f"sha256={actual_sha[:12]}…")
                return
            except Exception as exc:
                last = exc
                self.log(f"    [logs] transfer attempt {attempt}/3 failed: {exc}")
                self.chan = None
        self.preserve_logs = True
        container.keep()
        raise ArtifactTransferError(
            f"could not verify artifacts from {remote_dir} after 3 attempts: {last}")

    def teardown(self):
        try:
            if self.ssh:
                self.ssh.close()
        finally:
            if self.preserve_logs:
                self.log("[container] !!! ARTIFACT TRANSFER FAILED !!! recover /run/out "
                         "from the container before you stop it, or the sessions are lost.")
            else:
                self.log("[container] disconnected")


def _project_full_cost(run_dir: Path) -> None:
    from evalspec import FRAMEWORKS, MODELS

    task_count = len(all_prompt_ids())
    measured: dict[tuple[str, str], list[dict]] = {}
    for p in sorted(run_dir.glob("*/*/session_meta.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        if session_failed(m):
            continue
        cost = m.get("cost_usd")
        if cost is None:
            cost = session_cost(m["model_key"], m["tokens"].get("in", 0),
                                m["tokens"].get("out", 0))
        measured.setdefault((m["framework"], m["model_key"]), []).append(
            {"cost": cost, "wall": m.get("wallclock_s") or 0})

    if not measured:
        log("  (no sessions measured — cannot project)")
        return

    log("")
    log("=" * 64)
    log("FULL-RUN COST PROJECTION (from this pilot's measured sessions)")
    log("=" * 64)
    log(f"  {'cell':<28} {'$/session':>10} {'sessions':>9} {'subtotal':>10}")

    total = total_wall = 0.0
    missing = []
    for fw in FRAMEWORKS:
        for model_key in MODELS:
            cell = (fw["framework"], model_key)
            rows = measured.get(cell)
            if not rows:
                missing.append(f"{fw['key']}__{model_key}")
                continue
            per = sum(r["cost"] for r in rows) / len(rows)
            wall = sum(r["wall"] for r in rows) / len(rows)
            total += per * task_count
            total_wall += wall * task_count
            log(f"  {fw['key'] + '__' + model_key:<28} {per:>10.4f} {task_count:>9} "
                f"{per * task_count:>10.2f}")

    container_hours = total_wall / 3600.0
    judge_rows = []
    judgment_path = run_dir / "postprocess" / "completion_judgments.jsonl"
    if judgment_path.is_file():
        judge_rows = [json.loads(line) for line in judgment_path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
    judge_per = (sum(float(row.get("judge_cost_usd") or 0) for row in judge_rows)
                 / len(judge_rows) if judge_rows else 0.0)
    judge_total = judge_per * task_count * len(all_configs())
    log("  " + "-" * 60)
    log(f"  sessions      : ${total:.2f}   (range ${total:.2f}–${total * 2:.2f})")
    log(f"  answer judge  : ${judge_total:.2f} (blinded semantic judge)")
    log(f"  container time: ~{container_hours:.1f}h of session wallclock")
    log(f"  ESTIMATED FULL RUN: ${total + judge_total:.2f}–"
        f"${total * 2 + judge_total:.2f}")
    log("  (range because the pilot fixes one task; multi-step activities cost more)")
    if missing:
        log(f"  !! no measurement for: {', '.join(missing)} — excluded from the total")
    log("=" * 64)


def _validate_local_artifacts(run_dir: Path) -> bool:
    from post_experiment import snapshot_or_verify, validate_run
    checks = validate_run(run_dir)
    integrity = snapshot_or_verify(run_dir)
    post = run_dir / "postprocess"
    post.mkdir(exist_ok=True)
    (post / "log_validation.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    status = "PASS" if checks["ok"] else "FAIL"
    log(f"  log check   : {status} ({checks['found_sessions']} sessions, "
        f"{checks['artifact_receipts']} verified transfer receipts)")
    log(f"  raw hashes  : {integrity['status']} ({integrity['files']} immutable files)")
    for warning in checks["warnings"]:
        log(f"  log warning : {warning}")
    for failure in checks["failures"]:
        log(f"  log failure : {failure}")
    return checks["ok"] and integrity["status"] != "FAILED"


def _run_completion_judge(run_dir: Path) -> bool:
    from judge_attempts import run as judge_run
    try:
        log("Running blinded semantic completion judge (OpenRouter batch)...")
        judge_rc = judge_run(run_dir)
        if judge_rc != 0:
            raise RuntimeError(
                f"semantic judge batch is still pending (exit status {judge_rc}); "
                f"resume with: python judge_attempts.py \"{run_dir}\"")
        metrics = aggregate(run_dir)
        from post_experiment import make_figures
        make_figures(run_dir, metrics)
        completed = sum(bool(row.get("task_completed")) for row in (
            json.loads(line) for line in (run_dir / "postprocess" /
                                          "completion_judgments.jsonl").read_text(
                                              encoding="utf-8").splitlines()
            if line.strip()))
        log(f"  semantic completion: {completed}/{metrics['judged_sessions']} judged sessions")
        log(f"  judge cost         : ${metrics['judge_cost_usd']:.4f}")
        return metrics["judged_sessions"] == metrics["measurable_sessions"]
    except Exception as exc:
        log(f"[FATAL] semantic judge failed: {type(exc).__name__}: {exc}")
        return False


def _write_combined_report(run_dir: Path, env: dict) -> bool:
    raw = env.get("LEGACY_RUN_DIR", "").strip()
    if not raw:
        log("  combined report: skipped (LEGACY_RUN_DIR is not configured)")
        return True
    legacy = Path(raw).expanduser().resolve()
    if not legacy.is_dir():
        log(f"[FATAL] legacy run directory is unavailable: {legacy}")
        return False
    try:
        from combine_results import combine
        output = run_dir / f"combined_with_{legacy.name}"
        metrics = combine(legacy, run_dir, output)
        log(f"  combined report: {output / 'combined_metrics.md'}")
        log(f"  combined sessions: {metrics['overall_descriptive']['sessions']}")
        return True
    except Exception as exc:
        log(f"[FATAL] combined report failed: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    global _LOG_FH
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true",
                    help="16-session pilot (calculator + web in all eight cells)")
    ap.add_argument("--full", action="store_true", help="all 8 configs x selected GAIA prompt tasks")
    ap.add_argument("--resume-from", type=Path,
                    help="build a new full run from valid sessions in an earlier run")
    ap.add_argument("--containers", type=int, choices=(1, 2), default=1,
                    help="number of containers to spread the matrix across (default: 1, maximum: 2)")
    args = ap.parse_args()
    if not (args.pilot or args.full):
        ap.error("choose --pilot or --full")
    if args.resume_from and not args.full:
        ap.error("--resume-from requires --full")
    if args.resume_from and not args.resume_from.is_dir():
        ap.error(f"resume source is not a directory: {args.resume_from}")

    env = load_env()
    required = ["OPENROUTER_API_KEY", "TAVILY_API_KEY"]
    missing = [k for k in required if k not in env or not env[k]]
    if missing:
        ap.error(f"missing in config.env: {', '.join(missing)}")
    try:
        validate_tavily(env)
    except RuntimeError as exc:
        ap.error(str(exc))

    mode = "pilot" if args.pilot else "full"
    expected_sessions = build_sessions(mode)
    run_dir = REPO / "results" / f"run_{datetime.now():%Y-%m-%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FH = (run_dir / "run.log").open("w", encoding="utf-8")

    reused: list[dict] = []
    sessions = expected_sessions
    if args.resume_from:
        try:
            sessions, reused = _resume_sessions(args.resume_from.resolve(), run_dir,
                                                expected_sessions)
        except Exception as exc:
            log(f"[FATAL] cannot resume: {type(exc).__name__}: {exc}")
            return 1

    log("=" * 64)
    log(f"RELIABILITY EVAL — {mode.upper()} RUN")
    log("=" * 64)
    log(f"sessions   : {len(expected_sessions)}")
    log(f"containers : {args.containers} independent lane(s)")
    log("tavily     : live search preflight passed")
    if args.resume_from:
        log(f"resume from: {args.resume_from.resolve()}")
        log(f"reused      : {len(reused)} verified clean sessions")
        log(f"remaining   : {len(sessions)} sessions")
    log("where       : sessions run in a local Docker container")
    log(f"results dir : {run_dir}")
    log(f"live log    : {run_dir / 'run.log'}")
    log(f"tail it     : Get-Content -Wait '{run_dir / 'run.log'}'   (PowerShell)")
    log("=" * 64)

    completed_sessions = sum(not session_failed(m) for m in reused)
    total_cost = 0.0
    for m in reused:
        cost = m.get("cost_usd")
        if cost is None:
            cost = session_cost(m["model_key"], m.get("tokens", {}).get("in", 0),
                                m.get("tokens", {}).get("out", 0))
        total_cost += cost
    state = {"completed": completed_sessions, "failures": 0,
             "processed": len(reused), "cost": total_cost}
    state_lock = threading.Lock()
    aggregate_lock = threading.Lock()
    aborted = False

    if sessions:
        lanes = partition_config_lanes(sessions, args.containers, expected_sessions)
        container_count = len(lanes)
        for lane, lane_sessions in enumerate(lanes, 1):
            configs = list(dict.fromkeys(row[1] for row in lane_sessions))
            log(f"container {lane} cells: {', '.join(configs)}")
        container.start(REPO, "hbtl-section5-gaia", env, log,
                        passthrough=("OPENROUTER_API_KEY", "TAVILY_API_KEY"),
                        count=container_count)

        def run_container_lane(lane_index: int,
                         lane_sessions: list[tuple[int, str, str]]) -> bool:
            prefix = f"[pc{lane_index}]"
            lane_log = lambda message: log(f"{prefix} {message}")
            executor = None
            lane_aborted = False
            consecutive = 0
            durations: list[float] = []
            try:
                lane_log("Connecting to container...")
                executor = SSHExecutor(env, run_dir, lane_log, lane=lane_index)
                for lane_position, (global_i, config_id, prompt_id) in enumerate(
                        lane_sessions, 1):
                    started = time.time()
                    lane_log(f"[{global_i}/{len(expected_sessions)}] START "
                             f"{config_id} / {prompt_id}")
                    try:
                        out, retry_cost = run_one_with_retries(
                            executor, config_id, prompt_id)
                        with state_lock:
                            state["cost"] += retry_cost
                    except Exception as exc:
                        consecutive += 1
                        with state_lock:
                            state["failures"] += 1
                            state["processed"] += 1
                        lane_log(f"[{global_i}/{len(expected_sessions)}] ERROR running session: "
                                 f"{type(exc).__name__}: {exc} "
                                 f"(consecutive failures on this container: {consecutive})")
                        if isinstance(exc, ArtifactTransferError):
                            lane_log("[ABORT] evidence could not be verified locally; "
                                     "stopping this container lane immediately.")
                            lane_aborted = True
                            break
                        if consecutive >= MAX_CONSECUTIVE_FAILURES:
                            lane_log(f"[ABORT] {consecutive} failures in a row on this "
                                     "container; stopping its lane.")
                            lane_aborted = True
                            break
                        continue

                    duration = time.time() - started
                    durations.append(duration)
                    meta_path = out / "session_meta.json"
                    if not meta_path.exists():
                        consecutive += 1
                        with state_lock:
                            state["failures"] += 1
                            state["processed"] += 1
                        lane_log(f"[{global_i}/{len(expected_sessions)}] ERROR no "
                                 f"session_meta produced ({duration:.0f}s)")
                        if consecutive >= MAX_CONSECUTIVE_FAILURES:
                            lane_aborted = True
                            break
                        continue

                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    cost = meta.get("cost_usd")
                    if cost is None:
                        cost = session_cost(meta["model_key"],
                                            meta["tokens"].get("in", 0),
                                            meta["tokens"].get("out", 0))
                    with state_lock:
                        state["cost"] += cost
                        state["processed"] += 1
                    if session_failed(meta):
                        consecutive += 1
                        with state_lock:
                            state["failures"] += 1
                        reason = meta.get("error") or (
                            f"non-terminal state: {meta.get('agent_done')}")
                        lane_log(f"[{global_i}/{len(expected_sessions)}] FAILED "
                                 f"done={meta.get('agent_done')} ERR={reason} "
                                 f"${cost:.4f} | {duration:.0f}s "
                                 f"(consecutive failures: {consecutive})")
                        with aggregate_lock:
                            aggregate(run_dir)
                        if consecutive >= MAX_CONSECUTIVE_FAILURES:
                            lane_aborted = True
                            break
                        continue

                    consecutive = 0
                    with state_lock:
                        state["completed"] += 1
                        completed_now = state["completed"]
                        failures_now = state["failures"]
                        cost_now = state["cost"]
                    err = f" ERR={meta['error']}" if meta.get("error") else ""
                    remaining_lane = len(lane_sessions) - lane_position
                    eta = _fmt_eta((sum(durations) / len(durations)) * remaining_lane)
                    lane_log(f"[{global_i}/{len(expected_sessions)}] "
                             f"exact={meta.get('exact_answer_correct')!s:5s} "
                             f"done={meta['agent_done']:18s} turns={meta['turns_used']} "
                             f"${cost:.4f}{err} | {duration:.0f}s | running: "
                             f"completed={completed_now} fail={failures_now} "
                             f"${cost_now:.2f} lane-ETA~{eta}")
                    with aggregate_lock:
                        aggregate(run_dir)
            except Exception as exc:
                lane_log(f"[FATAL] container could not start: "
                         f"{type(exc).__name__}: {exc}")
                lane_aborted = True
            finally:
                lane_log("Disconnecting from this container...")
                try:
                    if executor:
                        executor.teardown()
                except Exception as exc:
                    lane_log(f"[warn] teardown error: {exc}")
            return lane_aborted

        with ThreadPoolExecutor(max_workers=container_count) as container_pool:
            futures = [container_pool.submit(run_container_lane, lane + 1, lane_sessions)
                       for lane, lane_sessions in enumerate(lanes)]
            lane_aborts = [future.result() for future in futures]
            aborted = any(lane_aborts)
    else:
        log("No sessions remain; validating the composite run without connecting to the container.")

    with state_lock:
        completed_sessions = state["completed"]
        failures = state["failures"]
        total_cost = state["cost"]

    metrics = aggregate(run_dir)
    artifacts_ok = _validate_local_artifacts(run_dir)
    log("")
    log("=" * 64)
    log("EVALUATION COMPLETE — sessions" + (" (ABORTED EARLY)" if aborted else ""))
    log(f"  ran        : {metrics['sessions']}/{len(expected_sessions)} sessions")
    log(f"  completed  : {completed_sessions}")
    log(f"  failures   : {failures}")
    log(f"  session $  : ${total_cost:.2f}")
    log(f"  results    : {run_dir}")
    log(f"  metrics    : {run_dir / 'metrics.md'}")
    log("=" * 64)

    if aborted or failures or not artifacts_ok:
        log("Run incomplete — NOT projecting cost. "
            "Fix the reported session or log-integrity failure(s) and re-run.")
        return 1

    if not _run_completion_judge(run_dir):
        log("Run has intact raw evidence, but semantic judging is incomplete. "
            "Rerun: python judge_attempts.py <results-dir>")
        return 1

    if args.pilot:
        _project_full_cost(run_dir)
        log("")
        log(f"[next] full run: python orchestrator.py --full --containers {args.containers}")
        return 0
    if not _write_combined_report(run_dir, env):
        return 1
    log("ALL DONE. Final metrics: " + str(run_dir / "metrics.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

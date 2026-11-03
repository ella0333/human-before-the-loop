from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import logging

from evalspec import (
    EXPERIMENT_CONTRACT_VERSION, FAKE_HOME, OPENCLAW_VERSION, all_configs,
    session_cost, session_measurable,
)
import container
from corpus_registry import CORPORA, injection_ids, pilot_injection_ids, rows as corpus_rows, spec as corpus_spec
from harness.aggregate import aggregate

for _n in ("httpx", "httpcore", "paramiko", "paramiko.transport", "urllib3"):
    logging.getLogger(_n).setLevel(logging.WARNING)

REPO = Path(__file__).resolve().parent
PILOT_INJECTION = "important_instructions__g_ssh"
PILOT_CONFIGS = [
    "openclaw_least_privilege__luna", "openclaw_least_privilege__oss",
    "openclaw_least_privilege__glimmer", "openclaw_least_privilege__spark",
]

MAX_CONSECUTIVE_FAILURES = 5
MAX_SESSION_ATTEMPTS = 5
MAX_OUTAGE_SESSION_ATTEMPTS = 10
UPSTREAM_OVERLOAD_COOLDOWN_SECONDS = 60
LAST_RUN_DIR: Path | None = None
UPSTREAM_OUTAGE_ROUND_PAUSE_SECONDS = 300
_LOG_FH = None


class ArtifactTransferError(RuntimeError):
    pass


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
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
        log(f"    [retry] invalid session attempt {attempt}/{attempt_cap}: "
            f"{reason}; pausing {wait_s}s ({pause_note}), then retrying the same session")
        time.sleep(wait_s)
    raise AssertionError("unreachable")


def load_env() -> dict:
    env = {}
    for envfile in (REPO.parents[2] / "config.env", REPO / ".env"):
        if not envfile.exists():
            continue
        for line in envfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    for k in ("OPENROUTER_API_KEY", "CONTAINER_SSH_HOST", "CONTAINER_SSH_PORT", "CONTAINER_SSH_USER",
              "SSH_KEY_PATH"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def all_injection_ids(corpus: str = "agentdojo_base") -> list[str]:
    return injection_ids(corpus)


def build_sessions(mode: str, corpus: str = "agentdojo_base") -> list[tuple[str, str]]:
    if mode == "pilot":
        return [(c, inj) for c in PILOT_CONFIGS for inj in pilot_injection_ids(corpus)]
    injs = all_injection_ids(corpus)
    return [(c["id"], inj) for c in all_configs() for inj in injs]


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

    for config_id, injection_id in expected:
        session_dir = source / config_id / injection_id
        meta_path = session_dir / "session_meta.json"
        receipt_path = session_dir / "artifact_receipt.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pending.append((config_id, injection_id))
            continue
        if (meta.get("experiment_contract_version") != EXPERIMENT_CONTRACT_VERSION
                or session_failed(meta) or raw_call_failed(session_dir)
                or not receipt_path.is_file() or not receipt_valid(session_dir)):
            pending.append((config_id, injection_id))
            continue
        target = destination / config_id / injection_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(session_dir, target)
        reused.append(meta)
        reused_ids.append(f"{config_id}__{injection_id}")
    (destination / "resume_manifest.json").write_text(json.dumps({
        "source": str(source.resolve()),
        "source_integrity": integrity,
        "reused_sessions": reused_ids,
        "sessions_to_run": [f"{c}__{i}" for c, i in pending],
    }, indent=2), encoding="utf-8")
    return pending, reused


class SSHExecutor:

    def __init__(self, env: dict, run_dir: Path, log_fn=print,
                 corpus: str = "agentdojo_base"):
        self.env = env
        self.run_dir = run_dir
        self.log = log_fn
        self.corpus = corpus
        self.key_path = os.path.expanduser(env.get("SSH_KEY_PATH", "~/.ssh/id_ed25519"))
        self.ssh = None
        self.chan = None
        self.preserve_logs = False
        host = env["CONTAINER_SSH_HOST"]
        port = int(env["CONTAINER_SSH_PORT"])
        user = env.get("CONTAINER_SSH_USER", "root")
        self._target = (host, port, user)
        self.log(f"[container] connecting to {user}@{host}:{port}")
        self._connect_ssh(host, port, user)
        self._wait_ready()
        self._verify_selected_corpus()
        self._verify_openclaw()

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

    def _verify_selected_corpus(self) -> None:
        if self.corpus == "agentdojo_base":
            return
        probe = (
            "cd /app && python run_session.py --help 2>&1 | grep -q -- '--corpus' "
            "&& test -f corpus/gray_swan_extension.jsonl "
            "&& test -f corpus/gray_swan_extension_canaries.json"
        )
        _out, rc = self._shell_run(probe, timeout=60)
        if rc != 0:
            raise RuntimeError(
                "The container image is older than the GraySwan extension. Publish the "
                "current repository through the existing build-image workflow, start a "
                "container from the new image, then rerun.")
        self.log("[container] GraySwan corpus selector and fixtures present in image")

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
                "tail -n 200 /run/injection-eval-startup.log 2>/dev/null; "
                "echo '--- processes ---'; ps -ef; "
                "echo '--- gateway status ---'; openclaw gateway status; "
                "echo '--- health ---'; openclaw health --json --timeout 5000", timeout=60)
            raise RuntimeError(
                f"container-owned gateway never became ready after {wait_s}s:\n"
                + diag.strip()[-6000:])
        self.log("[container] gateway HTTP readiness and documented health RPC ok")
        if OPENCLAW_VERSION not in nv:
            raise RuntimeError(f"expected stable OpenClaw {OPENCLAW_VERSION}, got: {nv.strip()}")
        for plugin_id in ("mock-discord",):
            out, rc = self._shell_run(
                f"openclaw plugins inspect {plugin_id} --runtime --json", timeout=120)
            if rc != 0:
                raise RuntimeError(
                    f"{plugin_id} plugin runtime inspection failed (rc={rc}): {out.strip()[-500:]}")
        out, rc = self._shell_run("cd /app && python tools/runtime_preflight.py", timeout=180)
        if rc != 0:
            raise RuntimeError(f"OpenClaw runtime preflight failed (rc={rc}):\n{out.strip()[-3000:]}")
        self.log("[container] stable OpenClaw, native-file policy, and mock-discord ingress ready")

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

    def run_one(self, config_id: str, injection_id: str) -> Path:
        rout = f"/run/out/{config_id}/{injection_id}"
        rhome = FAKE_HOME
        cmd = (f"cd /app && python run_session.py "
               f"--config-id {config_id} --injection-id {injection_id} "
               f"--out {rout} --home {rhome}")
        if self.corpus != "agentdojo_base":
            cmd += f" --corpus {self.corpus}"
        out, rc = self._shell_run(cmd, timeout=int(self.env.get("SESSION_TIMEOUT", "900")))
        if rc != 0:
            self.log(f"    [container] run_session rc={rc}: {out.strip()[-200:]}")
        local = self.run_dir / config_id / injection_id
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


def _project_full_cost(run_dir: Path, corpus: str = "agentdojo_base") -> None:
    from evalspec import FRAMEWORKS, MODELS

    n_inj = len(all_injection_ids(corpus))
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
            {"cost": cost, "wall": m.get("wallclock_s") or 0,
             "injection_id": m["injection_id"]})

    if not measured:
        log("  (no sessions measured — cannot project)")
        return

    log("")
    log("=" * 64)
    log("FULL-RUN COST PROJECTION (from this pilot's measured sessions)")
    log("=" * 64)
    log(f"  {'cell':<28} {'$/session':>10} {'sessions':>9} {'subtotal':>10}")

    prompt_lengths = {row["id"]: len(row["rendered_injection"])
                      for row in corpus_rows(corpus)}

    def interpolate(samples: list[dict], field: str, length: int) -> float:
        points = sorted((prompt_lengths.get(row["injection_id"], 0), float(row[field]))
                        for row in samples)
        if len(points) == 1 or length <= points[0][0]:
            return points[0][1]
        if length >= points[-1][0]:
            return points[-1][1]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= length <= x1:
                ratio = (length - x0) / max(1, x1 - x0)
                return y0 + ratio * (y1 - y0)
        return points[-1][1]

    if corpus == "gray_swan_extension":
        log("  measured plain framework/model cells by prompt length:")
        for cell, samples in sorted(measured.items()):
            details = sorted((prompt_lengths.get(row["injection_id"], 0), row["cost"])
                             for row in samples)
            log(f"    {cell[0]}__{cell[1]}: "
                + ", ".join(f"{length} chars=${cost:.4f}" for length, cost in details))

    total = total_wall = total_low = total_high = 0.0
    missing = []
    for fw in FRAMEWORKS:
        for model_key in MODELS:
            cell = (fw["framework"], model_key)
            rows = measured.get(cell)
            if not rows:
                missing.append(f"{fw['key']}__{model_key}")
                continue
            if corpus == "gray_swan_extension" and len(rows) >= 3:
                subtotal = sum(interpolate(rows, "cost", length)
                               for length in prompt_lengths.values())
                wall_subtotal = sum(interpolate(rows, "wall", length)
                                    for length in prompt_lengths.values())
                per = subtotal / n_inj
                wall = wall_subtotal / n_inj
            else:
                per = sum(r["cost"] for r in rows) / len(rows)
                wall = sum(r["wall"] for r in rows) / len(rows)
                subtotal, wall_subtotal = per * n_inj, wall * n_inj
            total += subtotal
            total_wall += wall_subtotal
            total_low += subtotal * (0.80 if corpus == "gray_swan_extension" else 1.0)
            total_high += subtotal * (1.25 if corpus == "gray_swan_extension" else 2.0)
            log(f"  {fw['key'] + '__' + model_key:<28} {per:>10.4f} {n_inj:>9} "
                f"{subtotal:>10.2f}")

    full_sessions = n_inj * len(all_configs())
    judge = 1.20 * full_sessions / 1056.0
    container_hours = total_wall / 3600.0
    log("  " + "-" * 60)
    log(f"  sessions      : ${total:.2f}   (range ${total_low:.2f}–${total_high:.2f})")
    log(f"  batch judge   : ~${judge:.2f}")
    log(f"  container time: ~{container_hours:.1f}h of session wallclock")
    log(f"  ESTIMATED FULL RUN: ${total_low + judge:.2f}–${total_high + judge:.2f}")
    if corpus == "gray_swan_extension":
        log("  (point estimate interpolates measured short/medium/long prompt costs; range is ±20/25%)")
    else:
        log("  (range because the pilot fixes ONE injection; multi-step goals cost more)")
    if missing:
        log(f"  !! no measurement for: {', '.join(missing)} — excluded from the total")
    log("=" * 64)
    if corpus == "gray_swan_extension":
        log("")
        log("GRAYSWAN EXTENSION COMMANDS")
        log(f"  planned prompts : {n_inj}")
        log(f"  full sessions   : {full_sessions} across {len(all_configs())} cells")
        log("  full            : python orchestrator.py --full --greyswan")
        log("  resume          : python orchestrator.py --full --greyswan --resume-from results\\run_<extension_timestamp>__gray_swan_extension")


def _print_plan(corpus: str) -> None:
    prompts = len(all_injection_ids(corpus))
    pilots = pilot_injection_ids(corpus)
    pilot_sessions = len(PILOT_CONFIGS) * len(pilots)
    full_sessions = len(all_configs()) * prompts
    print(f"corpus              : {corpus}")
    print(f"planned prompts     : {prompts}")
    print(f"pilot injections    : {len(pilots)} (short, medium, long)")
    print(f"pilot sessions      : {pilot_sessions}")
    print(f"full sessions       : {full_sessions} across {len(all_configs())} cells")
    if corpus == "gray_swan_extension":
        print("pilot command       : python orchestrator.py --pilot --greyswan")
        print("full command        : python orchestrator.py --full --greyswan")
        print("resume command      : python orchestrator.py --full --greyswan --resume-from results\\run_<extension_timestamp>__gray_swan_extension")
        print("full automatically classifies, post-processes, and writes the base comparison")


def _run_batch_classifier(run_dir: Path) -> int:
    log("")
    log("=" * 64)
    log("STEP 2 — attempt classification (OpenRouter batch, may take up to 24h)")
    log("=" * 64)
    proc = subprocess.run(
        [sys.executable, "post_experiment.py", str(run_dir), "--classify",
         "--max-wait", "86400"],
        cwd=str(REPO),
    )
    if proc.returncode != 0:
        log(f"[batch] post-processing exited {proc.returncode} — rerun resumes safely:")
        log(f"        python post_experiment.py {run_dir} --classify --max-wait 86400")
    return proc.returncode


def _run_combined_report(extension_run: Path) -> int:
    log("")
    log("=" * 64)
    log("STEP 3/3: combined report with the section 4 runs")
    log("=" * 64)
    log("python combined_matched_report.py results\\run_<agentdojo_timestamp> "
        f"{extension_run.relative_to(REPO)}")
    return 0


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


def run_corpus(argv: list[str] | None = None) -> int:
    global _LOG_FH, LAST_RUN_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="short pilot run for the selected corpus")
    ap.add_argument("--full", action="store_true", help="all configs x selected corpus")
    ap.add_argument("--plan", action="store_true", help="print session counts and exact commands; no API calls")
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="agentdojo_base")
    ap.add_argument("--greyswan", "--grayswan", dest="greyswan", action="store_true",
                    help="use the separate GraySwan extension corpus")
    ap.add_argument("--no-classify", action="store_true",
                    help="stop after sessions; run post_experiment.py yourself later")
    ap.add_argument("--resume-from", type=Path,
                    help="build a new full run from valid sessions in an earlier run")
    args = ap.parse_args(argv)
    if args.greyswan:
        if args.corpus != "agentdojo_base":
            ap.error("use --greyswan by itself; do not combine it with --corpus")
        args.corpus = "gray_swan_extension"
    if not (args.pilot or args.full or args.plan):
        ap.error("choose --pilot, --full, or --plan")
    if sum(bool(x) for x in (args.pilot, args.full, args.plan)) != 1:
        ap.error("choose exactly one of --pilot, --full, or --plan")
    if args.plan:
        _print_plan(args.corpus)
        return 0
    if args.resume_from and not args.full:
        ap.error("--resume-from requires --full")
    if args.resume_from and not args.resume_from.is_dir():
        ap.error(f"resume source is not a directory: {args.resume_from}")
    if args.resume_from:
        source_run_manifest = args.resume_from / "run_manifest.json"
        if args.corpus == "gray_swan_extension" and not source_run_manifest.is_file():
            ap.error(f"extension resume source lacks run_manifest.json: {args.resume_from}")
        if source_run_manifest.is_file():
            try:
                source_corpus = json.loads(source_run_manifest.read_text(
                    encoding="utf-8")).get("corpus")
            except (OSError, json.JSONDecodeError) as exc:
                ap.error(f"invalid resume run_manifest.json: {exc}")
            if source_corpus != args.corpus:
                ap.error(f"resume corpus mismatch: source={source_corpus!r}, selected={args.corpus!r}")

    env = load_env()
    required = ["OPENROUTER_API_KEY"]
    missing = [k for k in required if k not in env or not env[k]]
    if missing:
        ap.error(f"missing in config.env: {', '.join(missing)}. "
                 "Run `python setup.py` at the repository root.")

    mode = "pilot" if args.pilot else "full"
    expected_sessions = build_sessions(mode, args.corpus)
    suffix = "__gray_swan_extension" if args.corpus == "gray_swan_extension" else ""
    run_dir = REPO / "results" / f"run_{datetime.now():%Y-%m-%d_%H%M%S}{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    LAST_RUN_DIR = run_dir
    selected_rows = corpus_rows(args.corpus)
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "corpus": args.corpus,
        "corpus_source": corpus_spec(args.corpus)["corpus_source"],
        "study_phase": corpus_spec(args.corpus)["study_phase"],
        "prompt_count": len(selected_rows),
        "expected_sessions": len(expected_sessions),
        "source_revision": selected_rows[0].get("source_revision") if selected_rows else None,
    }, indent=2), encoding="utf-8")
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
    log(f"INJECTION EVAL — {mode.upper()} RUN")
    log("=" * 64)
    log(f"sessions   : {len(expected_sessions)}")
    log(f"corpus     : {args.corpus}")
    if args.resume_from:
        log(f"resume from: {args.resume_from.resolve()}")
        log(f"reused      : {len(reused)} verified clean sessions")
        log(f"remaining   : {len(sessions)} sessions")
    log("where       : sessions run in a local Docker container")
    log(f"results dir : {run_dir}")
    log(f"live log    : {run_dir / 'run.log'}")
    log(f"tail it     : Get-Content -Wait '{run_dir / 'run.log'}'   (PowerShell)")
    log("=" * 64)

    executor = None
    if sessions:
        log("Provisioning executor...")
        try:
            container.start(REPO, "hbtl-section4-least-privilege", env, log)
            executor = SSHExecutor(env, run_dir, log, corpus=args.corpus)
        except Exception as e:
            log(f"[FATAL] could not start executor: {type(e).__name__}: {e}")
            log("        (container SSH failed; nothing was charged for sessions.)")
            return 1
    else:
        log("No sessions remain; validating the composite run without connecting to the container.")

    succeeded = sum(bool(m.get("attack_succeeded", m.get("leaked"))) for m in reused)
    leaked = sum(bool(m.get("leaked")) for m in reused)
    failures = consecutive = 0
    total_cost = 0.0
    for m in reused:
        cost = m.get("cost_usd")
        if cost is None:
            cost = session_cost(m["model_key"], m.get("tokens", {}).get("in", 0),
                                m.get("tokens", {}).get("out", 0))
        total_cost += cost
    times: list[float] = []
    aborted = False
    try:
        for run_i, (config_id, injection_id) in enumerate(sessions, 1):
            i = len(reused) + run_i
            t0 = time.time()
            log(f"[{i}/{len(expected_sessions)}] START {config_id} / {injection_id}")
            try:
                out, retry_cost = run_one_with_retries(executor, config_id, injection_id)
                total_cost += retry_cost
            except Exception as e:
                failures += 1; consecutive += 1
                log(f"[{i}/{len(expected_sessions)}] ERROR running session: {type(e).__name__}: {e}  "
                    f"(consecutive failures: {consecutive})")
                if isinstance(e, ArtifactTransferError):
                    log("[ABORT] evidence could not be verified locally; stopping immediately.")
                    aborted = True
                    break
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    log(f"[ABORT] {consecutive} failures in a row — stopping to avoid burning money.")
                    aborted = True
                    break
                continue

            dt = time.time() - t0
            times.append(dt)
            meta_path = out / "session_meta.json"
            if not meta_path.exists():
                failures += 1; consecutive += 1
                log(f"[{i}/{len(expected_sessions)}] ERROR no session_meta produced ({dt:.0f}s)  "
                    f"(consecutive: {consecutive})")
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    log(f"[ABORT] {consecutive} failures in a row — stopping.")
                    aborted = True
                    break
                continue

            m = json.loads(meta_path.read_text(encoding="utf-8"))
            cost = m.get("cost_usd")
            if cost is None:
                cost = session_cost(m["model_key"], m["tokens"].get("in", 0), m["tokens"].get("out", 0))
            total_cost += cost
            if session_failed(m):
                failures += 1; consecutive += 1
                failure_reason = m.get("error") or f"non-terminal state: {m.get('agent_done')}"
                log(f"[{i}/{len(expected_sessions)}] FAILED done={m.get('agent_done')} "
                    f"ERR={failure_reason} ${cost:.4f} | {dt:.0f}s "
                    f"(consecutive failures: {consecutive})")
                aggregate(run_dir)
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    log(f"[ABORT] {consecutive} failures in a row — stopping to avoid burning money.")
                    aborted = True
                    break
                continue
            consecutive = 0
            if m["leaked"]:
                leaked += 1
            if m.get("attack_succeeded", m["leaked"]):
                succeeded += 1
            err = f" ERR={m['error']}" if m.get("error") else ""
            remaining = len(sessions) - run_i
            eta = _fmt_eta((sum(times) / len(times)) * remaining) if times else "?"
            log(f"[{i}/{len(expected_sessions)}] {m['outcome']:14s} success={m.get('attack_succeeded', m['leaked'])!s:5s} "
                f"leaked={m['leaked']!s:5s} "
                f"done={m['agent_done']:18s} turns={m['turns_used']} ${cost:.4f}{err}  "
                f"| {dt:.0f}s | running: success={succeeded} leaked={leaked} fail={failures} ${total_cost:.2f} ETA~{eta}")
            aggregate(run_dir)
    finally:
        log("Disconnecting from the container...")
        try:
            if executor:
                executor.teardown()
        except Exception as e:
            log(f"[warn] teardown error: {e}")

    metrics = aggregate(run_dir)
    artifacts_ok = _validate_local_artifacts(run_dir)
    log("")
    log("=" * 64)
    log("STEP 1/2 COMPLETE — sessions" + (" (ABORTED EARLY)" if aborted else ""))
    log(f"  ran        : {metrics['sessions']}/{len(expected_sessions)} sessions")
    log(f"  succeeded  : {succeeded}")
    log(f"  leaked     : {leaked}")
    log(f"  failures   : {failures}")
    log(f"  session $  : ${total_cost:.2f}")
    log(f"  results    : {run_dir}")
    log(f"  metrics    : {run_dir / 'metrics.md'}")
    log("=" * 64)

    if aborted or failures or not artifacts_ok:
        log("Run incomplete — NOT projecting cost or starting the batch classifier. "
            "Fix the reported session or log-integrity failure(s) and re-run.")
        return 1

    if args.pilot:
        _project_full_cost(run_dir, args.corpus)
        log("")
        log("[next] full run (both corpora and the combined report): python orchestrator.py --full")
        log(f"[next] judge these pilot sessions (optional): "
            f"python post_experiment.py {run_dir} --classify")
        return 0

    if args.no_classify:
        log(f"[next] when ready: python post_experiment.py {run_dir} --classify")
        return 0

    classifier_rc = _run_batch_classifier(run_dir)
    if classifier_rc != 0:
        log("Sessions and raw evidence are safe locally; classification is not complete yet.")
        return classifier_rc
    if args.corpus == "gray_swan_extension":
        combined_rc = _run_combined_report(run_dir)
        if combined_rc != 0:
            log("Extension sessions, classification, and post-processing are complete; "
                "only the combined report needs to be retried.")
            return combined_rc
    log("")
    log("ALL DONE. Final metrics: " + str(run_dir / "metrics.md"))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    single = {"--greyswan", "--grayswan", "--corpus", "--resume-from", "--plan"} & set(argv)
    if single or not {"--pilot", "--full"} & set(argv):
        return run_corpus(argv)
    full = "--full" in argv
    skip_classify = "--no-classify" in argv
    base_argv = [arg for arg in argv if arg != "--no-classify"] + (["--no-classify"] if full else [])
    rc = run_corpus(base_argv)
    base_run = LAST_RUN_DIR
    if rc:
        return rc
    rc = run_corpus(base_argv + ["--greyswan"])
    extension_run = LAST_RUN_DIR
    if rc or not full:
        return rc
    if skip_classify:
        log(f"[next] python post_experiment.py {base_run} --classify")
        log(f"[next] python post_experiment.py {extension_run} --classify")
        log(f"[next] python combined_matched_report.py {base_run} {extension_run}")
        return 0
    for run_dir in (base_run, extension_run):
        rc = _run_batch_classifier(run_dir)
        if rc:
            log(f"Classification for {run_dir} is not complete; rerun "
                f"python post_experiment.py {run_dir} --classify, then "
                f"python combined_matched_report.py {base_run} {extension_run}")
            return rc
    log("")
    log("=" * 64)
    log("COMBINED REPORT")
    log("=" * 64)
    proc = subprocess.run([sys.executable, "combined_matched_report.py",
                           str(base_run), str(extension_run)], cwd=str(REPO))
    if proc.returncode == 0:
        log("ALL DONE. Combined results are in the combined_matched folder printed above.")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

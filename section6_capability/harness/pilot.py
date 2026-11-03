from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import container
from evalspec import FULL_CAPS, PILOT_CAPS, CONTAINER_FULL_CAPS
from credit_gate import remaining

ROOT = Path(__file__).resolve().parent
CONSTRAINED_CELLS = ("constrained__glimmer", "constrained__spark")
TOOL_CALLING_CELLS = ("tool_calling__glimmer", "tool_calling__spark")
CONTAINER_CELLS = ("continual_harness__glimmer", "continual_harness__spark")
ALL_CELLS = CONSTRAINED_CELLS + TOOL_CALLING_CELLS + CONTAINER_CELLS
LOCAL_RUNNERS = {
    "constrained": ("constrained.run_local", "--output"),
    "tool_calling": ("tool_calling.run_local", "--out"),
}
for name in ("httpx", "paramiko", "urllib3"):
    logging.getLogger(name).setLevel(logging.WARNING)

_LOG_FH = None


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (ROOT.parent.parent / "config.env", ROOT / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def log(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {message}"
    print(line, flush=True)
    if _LOG_FH:
        _LOG_FH.write(line + "\n")
        _LOG_FH.flush()


def seed_combined_results(run_dir: Path, source: Path,
                          fresh_models: tuple[str, ...],
                          fresh_framework: str = "continual_harness") -> None:
    source = source if source.is_absolute() else ROOT / source
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"combined-results source does not exist: {source}")
    fresh_cells = [f"{fresh_framework}__{model}" for model in fresh_models]
    copied: list[str] = []
    for cell in ALL_CELLS:
        if cell in fresh_cells:
            continue
        cell_source = source / cell
        if not (cell_source / "session_meta.json").is_file():
            raise SystemExit(f"combined-results source is missing {cell}/session_meta.json")
        meta = json.loads((cell_source / "session_meta.json").read_text(encoding="utf-8"))
        if meta.get("success") is not True:
            raise SystemExit(f"refusing to carry unsuccessful combined-results cell: {cell}")
        shutil.copytree(cell_source, run_dir / cell)
        copied.append(cell)
    (run_dir / "combined_from.json").write_text(json.dumps({
        "source": str(source),
        "copied_cells": copied,
        "fresh_cells": fresh_cells,
        "created_at": datetime.now().isoformat(),
    }, indent=2), encoding="utf-8")


class ContainerLane:
    def __init__(self, env: dict[str, str], run_dir: Path):
        self.env, self.run_dir = env, run_dir
        self.preserve_logs = False
        self.key_path = os.path.expanduser(env.get("SSH_KEY_PATH", "~/.ssh/id_ed25519"))
        self.ssh = self.channel = None
        host = env["CONTAINER_SSH_HOST"]
        port = int(env["CONTAINER_SSH_PORT"])
        user = env.get("CONTAINER_SSH_USER", "root")
        self.target = (host, port, user)
        log(f"[container] connecting to {user}@{host}:{port}")
        try:
            self._connect_shell()
            self._wait_ready()
            output, rc = self.shell("cd /app && python tools/runtime_preflight.py", 180)
            if rc:
                raise RuntimeError(f"runtime preflight failed: {output[-3000:]}")
            log("[container] Continual Harness/Red/provider policy preflight passed")
            rom = env.get("LOCAL_ROM_PATH")
            if not rom:
                raise RuntimeError("LOCAL_ROM_PATH is not set in config.env; "
                                   "supply your own Pokemon Red ROM")
            self._upload_rom(Path(rom))
        except BaseException:
            self.teardown()
            raise

    def _connect_shell(self) -> None:
        import paramiko
        last = None
        for attempt in range(20):
            try:
                self.ssh = paramiko.SSHClient(); self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh.connect(hostname=self.target[0], port=self.target[1], username=self.target[2],
                                 key_filename=self.key_path, timeout=30, look_for_keys=False,
                                 allow_agent=False, banner_timeout=60, auth_timeout=60)
                self.channel = self.ssh.invoke_shell(width=1000, height=400); self.channel.settimeout(1)
                time.sleep(2); self._drain()
                self.shell("export PS1=''; stty -echo 2>/dev/null; export TERM=dumb", 20, ensure=False)
                output, rc = self.shell("echo SHELL__OK", 30, ensure=False)
                if rc == 0 and "SHELL__OK" in output:
                    log(f"[container] SSH ready at {self.target[2]}@{self.target[0]}:{self.target[1]}")
                    return
                raise RuntimeError("shell probe failed")
            except Exception as exc:
                last = exc; time.sleep(min(10 + attempt * 5, 30))
        raise RuntimeError(f"SSH failed: {last}")

    def _drain(self) -> str:
        data = ""
        while True:
            try:
                chunk = self.channel.recv(65536)
                if not chunk: break
                data += chunk.decode("utf-8", "ignore")
            except Exception:
                break
        return data

    def shell(self, command: str, timeout: int = 900, ensure: bool = True,
              live_prefix: str | None = None,
              extend_on_credit_pause: bool = False) -> tuple[str, int]:
        if ensure and (not self.channel or self.channel.closed):
            self._connect_shell()
        tag = str(time.time_ns())
        self.channel.send(f"M=RC_{tag}; {command}; printf '\\n__%s_%s__\\n' \"$M\" \"$?\"\n")
        end = re.compile(r"__RC_" + tag + r"_(\d+)__")
        data, deadline, shown = "", time.time() + timeout, 0
        while time.time() < deadline:
            try:
                chunk = self.channel.recv(65536)
                if chunk:
                    decoded = chunk.decode("utf-8", "ignore")
                    data += decoded
                    if extend_on_credit_pause and "CREDIT PAUSE:" in decoded:
                        deadline = max(deadline, time.time() + timeout)
                    if live_prefix:
                        pending = data[shown:]
                        complete = pending.rsplit("\n", 1)
                        if len(complete) == 2:
                            for line in complete[0].splitlines():
                                if line.strip() and not line.startswith("M=RC_"):
                                    log(f"[{live_prefix}] {line.rstrip()}")
                            shown = len(data) - len(complete[1])
                    match = end.search(data)
                    if match: return data[:match.start()], int(match.group(1))
            except Exception:
                time.sleep(0.2)
        return data, -1

    def _wait_ready(self) -> None:
        for index in range(80):
            output, rc = self.shell("cd /app 2>/dev/null && test -f continual_eval/run_remote.py && test -f /run/pokemon-eval/ready && echo READY", 20)
            if rc == 0 and "READY" in output:
                return
            if index % 2 == 0: log(f"[container] image/Continual Harness booting ({index * 15}s)")
            time.sleep(15)
        raise RuntimeError("container image/Gateway readiness timeout")

    def _upload_rom(self, path: Path) -> None:
        if not path.is_file(): raise RuntimeError(f"local ROM missing: {path}")
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        self.shell("install -d -m 700 /run/pokemon-eval/rom && : > /run/pokemon-eval/rom/upload.b64", 30)
        for offset in range(0, len(payload), 24000):
            chunk = payload[offset:offset + 24000]
            _, rc = self.shell(f"printf '%s' '{chunk}' >> /run/pokemon-eval/rom/upload.b64", 30)
            if rc: raise RuntimeError("ROM upload chunk failed")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        output, rc = self.shell("base64 -d /run/pokemon-eval/rom/upload.b64 > /run/pokemon-eval/rom/PokemonRed.gb && rm /run/pokemon-eval/rom/upload.b64 && sha256sum /run/pokemon-eval/rom/PokemonRed.gb", 60)
        if rc or expected not in output: raise RuntimeError("ROM upload checksum mismatch")
        log(f"[container] ephemeral ROM transfer verified sha256={expected[:12]}…")

    def run(self, model: str, mode: str) -> Path:
        remote = f"/run/out/continual_harness__{model}"
        caps = PILOT_CAPS if mode == "pilot" else CONTAINER_FULL_CAPS
        default_timeout = int(caps["wallclock_seconds"]) + 30 * 60
        timeout = int(self.env.get("SESSION_TIMEOUT", str(default_timeout)))
        output, rc = self.shell(
            f"cd /app && python -m continual_eval.run_remote --mode {mode} --model {model} "
            f"--rom /run/pokemon-eval/rom/PokemonRed.gb --out {remote}",
            timeout, live_prefix=f"container/{model}", extend_on_credit_pause=True)
        if rc: log(f"[container/{model}] runner rc={rc}: {output[-500:]}")
        local = self.run_dir / f"continual_harness__{model}"; local.mkdir(parents=True)
        try:
            self._pull(remote, local)
        except Exception:
            self.preserve_logs = True
            container.keep()
            raise
        if rc:
            raise RuntimeError(f"container/{model} runner failed with rc={rc}; artifacts preserved")
        return local

    def _pull(self, remote: str, local: Path) -> None:
        output, rc = self.shell(f"tar -C {remote} -czf /run/pokemon-result.tgz . && sha256sum /run/pokemon-result.tgz && printf '\\nARTBEG\\n' && base64 /run/pokemon-result.tgz && printf 'ARTEND\\n'", 600)
        match = re.search(r"([0-9a-f]{64})\s+/run/pokemon-result.tgz.*?ARTBEG\r?\n(.*?)ARTEND", output, re.S)
        if rc or not match: raise RuntimeError("artifact transfer envelope missing")
        payload = re.sub(r"[^A-Za-z0-9+/=]", "", match.group(2)); data = base64.b64decode(payload)
        if hashlib.sha256(data).hexdigest() != match.group(1): raise RuntimeError("artifact checksum mismatch")
        with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
            root = local.resolve()
            for member in archive.getmembers():
                target = (local / member.name).resolve()
                if member.issym() or member.islnk() or (target != root and root not in target.parents):
                    raise RuntimeError(f"unsafe artifact member: {member.name}")
            archive.extractall(local)
        files = [{"path": p.relative_to(local).as_posix(), "bytes": p.stat().st_size,
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in local.rglob("*") if p.is_file()]
        (local / "artifact_receipt.json").write_text(json.dumps({
            "archive_sha256": match.group(1), "files": files,
            "verified_at": datetime.now().isoformat()}, indent=2), encoding="utf-8")
        log(f"[container] immediately verified {len(files)} artifacts for {local.name}")

    def teardown(self) -> None:
        if self.ssh:
            try: self.ssh.close()
            except Exception: pass
        if self.preserve_logs:
            log("[container] ARTIFACT TRANSFER FAILED — recover /run/out from the "
                "container before you stop it, or the cells are lost")
        else:
            log("[container] disconnected")

def local_lane(run_dir: Path, env: dict[str, str], mode: str,
               frameworks: tuple[str, ...] = ("constrained", "tool_calling"),
               models: tuple[str, ...] = ("glimmer", "spark")) -> None:
    failures = []
    for framework in frameworks:
      module, out_flag = LOCAL_RUNNERS[framework]
      for model in models:
        output = run_dir / f"{framework}__{model}"
        tag = f"local/{framework}/{model}"
        log(f"[{tag}] starting sequential cell")
        process = subprocess.Popen(
            [sys.executable, "-m", module, "--mode", mode,
             "--model", model, out_flag, str(output)],
            cwd=ROOT, env={**os.environ, **env}, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert process.stdout is not None
        for line in process.stdout:
            log(f"[{tag}] {line.rstrip()}")
        rc = process.wait()
        log(f"[{tag}] finished rc={rc}; artifacts={output}")
        if rc:
            failures.append(f"{tag} rc={rc}")
            log(f"[{tag}] recorded failure; continuing to the next cell as in the proven evals")
    if failures:
        raise RuntimeError("; ".join(failures))


def remote_lane(run_dir: Path, env: dict[str, str], mode: str,
                models: tuple[str, ...] = ("glimmer", "spark")) -> None:
    lane = ContainerLane(env, run_dir)
    failures = []
    try:
        for model in models:
            log(f"[container/{model}] starting sequential cell on reused container")
            try:
                lane.run(model, mode)
            except RuntimeError as exc:
                if lane.preserve_logs:
                    raise
                if mode == "pilot":
                    log(f"[container/{model}] pilot cell failed; aborting remaining container cells")
                    raise
                failures.append(str(exc))
                log(f"[container/{model}] recorded failure; continuing to the next model as in the proven evals")
    finally:
        lane.teardown()
    if failures:
        raise RuntimeError("; ".join(failures))


def main(argv: list[str] | None = None) -> int:
    global _LOG_FH
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--container-only", action="store_true")
    parser.add_argument("--tool-calling-only", action="store_true")
    parser.add_argument("--combine-with", type=Path)
    parser.add_argument("--model", choices=("glimmer", "spark"))
    args = parser.parse_args(argv)
    rerun_lane = args.container_only or args.tool_calling_only
    if args.combine_with and (args.mode != "full" or not rerun_lane):
        parser.error("--combine-with requires --mode full with --container-only or --tool-calling-only")
    if args.model and not rerun_lane:
        parser.error("--model requires --container-only or --tool-calling-only")
    env = load_env()
    if not args.container_only and not (ROOT / "local_runtime/baseline_red/source_manifest.json").is_file():
        raise SystemExit("stage local runtime first: python tools/stage_local_runtime.py")
    required = {"OPENROUTER_API_KEY"}
    if not (args.local_only or args.tool_calling_only):
        required |= {"LOCAL_ROM_PATH"}
    missing = sorted(key for key in required if not env.get(key))
    if missing:
        raise SystemExit(f"missing in config.env: {', '.join(missing)}. "
                         "Run `python setup.py` at the repository root.")
    run_dir = ROOT / "results" / f"{args.mode}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True)
    container_models = (args.model,) if args.model else ("glimmer", "spark")
    fresh_framework = "tool_calling" if args.tool_calling_only else "continual_harness"
    if args.combine_with:
        seed_combined_results(run_dir, args.combine_with, container_models, fresh_framework)
    _LOG_FH = (run_dir / "run.log").open("w", encoding="utf-8")
    try:
        key_remaining = remaining(env["OPENROUTER_API_KEY"])
    except Exception as exc:
        log(f"[safety] REFUSING TO START: could not verify OpenRouter key limit: "
            f"{type(exc).__name__}: {exc}")
        _LOG_FH.close(); _LOG_FH = None
        return 2
    if key_remaining is None or key_remaining <= 0:
        detail = "no finite per-key limit" if key_remaining is None else f"limit_remaining={key_remaining}"
        log(f"[safety] REFUSING TO START: OpenRouter key has {detail}. "
            "Set/raise the key's USD spending limit first.")
        _LOG_FH.close(); _LOG_FH = None
        return 2
    log(f"[safety] OpenRouter per-key spending limit active: "
        f"limit_remaining=${key_remaining:.2f}")
    (run_dir / "safety.json").write_text(json.dumps({
        "openrouter_limit_remaining_at_launch_usd": key_remaining,
        "meaning": "hard maximum model spend before OpenRouter credit pause unless the user raises the key limit",
    }, indent=2), encoding="utf-8")
    caps = (PILOT_CAPS if args.mode == "pilot" else
            (CONTAINER_FULL_CAPS if args.container_only else FULL_CAPS))
    if args.mode == "pilot":
        scope = f"container {args.model}" if args.model else "requested cells"
        log(f"[pilot] {scope} five-model-call cost/validity run; "
            f"safety caps calls={caps['model_calls']}, turns={caps['turns']}, "
            f"wall={caps['wallclock_seconds']}s")
    else:
        scope = "combined six-cell" if args.combine_with else "six-cell"
        log(f"[full] {scope} tutorial-completion run; per-cell caps: "
            f"calls={caps['model_calls']}, turns={caps['turns']}, "
            f"wall={caps['wallclock_seconds']}s")
        if args.combine_with:
            fresh_cells = {f"{fresh_framework}__{model}" for model in container_models}
            retained = [cell for cell in ALL_CELLS if cell not in fresh_cells]
            log(f"[full] reused {', '.join(retained)} from {args.combine_with}; "
                f"running fresh {fresh_framework} {', '.join(container_models)} only")
    if not (args.local_only or args.tool_calling_only):
        container.start(ROOT, "hbtl-section6", env, log)
    run_error: BaseException | None = None
    summary_invalid = False
    try:
        jobs = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            if args.tool_calling_only:
                jobs.append(pool.submit(local_lane, run_dir, env, args.mode,
                                        ("tool_calling",), container_models))
            elif not args.container_only:
                jobs.append(pool.submit(local_lane, run_dir, env, args.mode))
            if not (args.local_only or args.tool_calling_only):
                jobs.append(pool.submit(remote_lane, run_dir, env, args.mode, container_models))
            for future in jobs:
                try:
                    future.result()
                except BaseException as exc:
                    run_error = run_error or exc
    finally:
        if args.mode == "pilot":
            summary_command = [sys.executable, "tools/summarize_pilot.py", str(run_dir)]
            if args.container_only:
                summary_command.extend(("--expected-framework", "continual_harness"))
            elif args.tool_calling_only:
                summary_command.extend(("--expected-framework", "tool_calling"))
            elif args.local_only:
                summary_command.extend(("--expected-framework", "constrained", "tool_calling"))
            if args.model:
                summary_command.extend(("--expected-model", args.model))
            process = subprocess.Popen(
                summary_command, cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert process.stdout is not None
            for line in process.stdout:
                log(f"[pilot-summary] {line.rstrip()}")
            summary_rc = process.wait()
            if summary_rc and run_error is None:
                summary_invalid = True
                log("[pilot] INVALID: one or more cells failed the model-call/vision/session/action validity contract; "
                    "this is a bounded eval failure, not a process crash.")
        log(f"{args.mode} {'failed' if run_error or summary_invalid else 'complete'}: {run_dir}")
        _LOG_FH.close()
        _LOG_FH = None
    if run_error:
        raise run_error
    return 1 if summary_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())

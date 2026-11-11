"""Build this harness's image and run it as a local Docker container."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
from pathlib import Path

_keep = False


def keep() -> None:
    global _keep
    _keep = True


def _remove(container_id: str) -> None:
    if _keep:
        print(f"[container] left {container_id[:12]} running so /run/out can be recovered; "
              f"remove it afterwards with: docker rm -f {container_id[:12]}")
        return
    subprocess.run(["docker", "rm", "-f", container_id],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _docker(*args: str, capture: bool = True, env: dict | None = None):
    return subprocess.run(["docker", *args], capture_output=capture, text=True, env=env)


def _require_docker() -> None:
    if not shutil.which("docker"):
        raise SystemExit("Docker is not installed. Install Docker Desktop, start it, then rerun.")
    if _docker("info").returncode != 0:
        raise SystemExit("Docker is installed but not running. Start Docker Desktop, then rerun.")


def _container_key(harness: Path) -> tuple[str, str]:
    import paramiko

    path = harness / "keys" / "container_key"
    if not path.is_file():
        path.parent.mkdir(exist_ok=True)
        paramiko.RSAKey.generate(3072).write_private_key_file(str(path))
    key = paramiko.RSAKey.from_private_key_file(str(path))
    return str(path), f"{key.get_name()} {key.get_base64()}"


def start(harness: Path, image: str, env: dict, log,
          passthrough: tuple[str, ...] = ("OPENROUTER_API_KEY",), count: int = 1) -> None:
    _require_docker()
    log(f"[container] building the {image} image "
        "(the first build takes several minutes; later runs reuse it)")
    if _docker("build", "-t", image, str(harness), capture=False).returncode != 0:
        raise SystemExit(f"docker build failed for {image}; the output above says why")
    key_path, public_key = _container_key(harness)
    run_env = {**os.environ, "PUBLIC_KEY": public_key,
               **{name: env[name] for name in passthrough}}
    for lane in range(1, count + 1):
        args = ["run", "-d", "-p", "127.0.0.1::22", "-e", "PUBLIC_KEY"]
        for name in passthrough:
            args += ["-e", name]
        started = _docker(*args, image, env=run_env)
        if started.returncode != 0:
            raise SystemExit(f"docker run failed for {image}: {started.stderr.strip()}")
        container_id = started.stdout.strip()
        atexit.register(_remove, container_id)
        mapped = _docker("port", container_id, "22/tcp").stdout.split()
        if not mapped:
            logs = _docker("logs", "--tail", "40", container_id)
            raise SystemExit(f"the {image} container exited right after starting:\n"
                             f"{(logs.stdout + logs.stderr).strip()}")
        port = mapped[0].rsplit(":", 1)[1]
        suffix = "" if lane == 1 else f"_{lane}"
        env[f"CONTAINER_SSH_HOST{suffix}"] = "127.0.0.1"
        env[f"CONTAINER_SSH_PORT{suffix}"] = port
        log(f"[container] started {container_id[:12]} on 127.0.0.1:{port}; "
            "it is removed when the run exits")
    env["CONTAINER_SSH_USER"] = "root"
    env["SSH_KEY_PATH"] = key_path

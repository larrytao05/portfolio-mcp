#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
RUNS = Path(tempfile.gettempdir()) / "portfolio-fixture-verification"
EVIDENCE = ROOT / ".audit" / "verification-evidence"


def environment(run_dir):
    safe = ("PATH", "HOME", "TMPDIR", "SYSTEMROOT", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in safe if key in os.environ}
    env.update(
        PORTFOLIO_PROVIDER="fixture",
        MARKET_DATA_PROVIDER="fixture",
        EXECUTION_PROVIDER="fixture",
        SCHWAB_EXECUTION_ENABLED="false",
        PORTFOLIO_DATABASE_URL=f"sqlite:///{run_dir / 'portfolio.sqlite'}",
        PYTHONPATH=str(ROOT),
        PYTHONUNBUFFERED="1",
    )
    return env


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stamp(pid):
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def load(run_dir):
    run_dir = run_dir.resolve()
    if run_dir.parent != RUNS.resolve():
        raise ValueError("Use only the RUN_DIR printed by this helper.")
    state = json.loads((run_dir / "state.json").read_text())
    if (
        state["root"] != str(ROOT)
        or state["run_dir"] != str(run_dir)
        or state["evidence_dir"] != str(EVIDENCE / run_dir.name)
        or state["mode"] != "fixture"
    ):
        raise ValueError("Run state does not match this checkout.")
    for key in ("api_port", "dashboard_port"):
        if type(state[key]) is not int or not 1024 <= state[key] <= 65535:
            raise ValueError("Invalid verification port.")
    return state


def stop(state):
    for item in reversed(state["processes"]):
        pid = item["pid"]
        current = stamp(pid)
        if not current:
            continue
        if pid <= 1 or pid == os.getpgrp() or current != item["stamp"]:
            raise RuntimeError("Process identity changed; refusing to signal it.")
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        deadline = time.monotonic() + 4
        while stamp(pid) == current and time.monotonic() < deadline:
            time.sleep(0.1)
        if stamp(pid) == current:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def get(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.read()


def doctor(state):
    if len(state["processes"]) != 2:
        raise RuntimeError("Two fixture service processes must be recorded.")
    for name, port in (
        ("api", state["api_port"]),
        ("dashboard", state["dashboard_port"]),
    ):
        process = next(item for item in state["processes"] if item["name"] == name)
        if not process["stamp"] or stamp(process["pid"]) != process["stamp"]:
            raise RuntimeError(f"The {name} process is not running.")
        listeners = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [int(value) for value in listeners.stdout.split() if value.isdigit()]
        if not pids or any(os.getpgid(pid) != process["pid"] for pid in pids):
            raise RuntimeError(f"The {name} port is not owned by this run.")
    api = f"http://127.0.0.1:{state['api_port']}"
    web = f"http://127.0.0.1:{state['dashboard_port']}"
    if json.loads(get(f"{api}/api/health")) != {"status": "ok"}:
        raise RuntimeError("API health failed.")
    if b"Portfolio Dashboard" not in get(web):
        raise RuntimeError("Unexpected dashboard page.")
    if json.loads(get(f"{api}/api/accounts")) != json.loads(get(f"{web}/api/accounts")):
        raise RuntimeError("Dashboard proxy targets a different API.")
    json.loads(get(f"{web}/api/overview"))["overview"]["status"]
    settings = json.loads(get(f"{api}/api/trading/settings"))["settings"]
    if settings["live_trading_enabled"] or not settings["kill_switch_active"]:
        raise RuntimeError(
            "Safeguards differ from the blocked fixture launch baseline."
        )


def vite_config(run_dir, api_port):
    config = {
        "root": str(ROOT / "dashboard"),
        "envDir": str(run_dir),
        "envPrefix": "VERIFY_PUBLIC_",
        "cacheDir": str(run_dir / "vite-cache"),
        "server": {"proxy": {"/api": f"http://127.0.0.1:{api_port}"}},
    }
    package = json.dumps(str(ROOT / "dashboard/package.json"))
    path = run_dir / "vite.config.mjs"
    path.write_text(
        'import {createRequire} from "node:module";\n'
        f"const require = createRequire({package});\n"
        "const plugin = require.resolve('@vitejs/plugin-react');\n"
        "const react = (await import(plugin)).default;\n"
        f"export default {{...{json.dumps(config)}, plugins: [react()]}};\n"
    )
    return path


def cleanup(state):
    stop(state)
    run_dir = Path(state["run_dir"])
    for log in run_dir.glob("*.log"):
        shutil.copyfile(log, Path(state["evidence_dir"]) / log.name)
    shutil.rmtree(run_dir)
    print(f"Scratch removed; evidence remains at {state['evidence_dir']}")


def start(history):
    python = ROOT / ".venv/bin/python"
    vite = ROOT / "dashboard/node_modules/vite/bin/vite.js"
    if not python.is_file() or not vite.is_file() or not shutil.which("lsof"):
        raise RuntimeError(
            "Install locked Python/dashboard dependencies and lsof first."
        )
    RUNS.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=RUNS)).resolve()
    evidence_dir = EVIDENCE / run_dir.name
    evidence_dir.mkdir(parents=True)
    state = {
        "root": str(ROOT),
        "run_dir": str(run_dir),
        "evidence_dir": str(evidence_dir),
        "mode": "fixture",
        "history_seeded": history,
        "processes": [],
    }
    print(f"RUN_DIR={run_dir}", flush=True)
    print(f"EVIDENCE_DIR={evidence_dir}", flush=True)
    try:
        state["api_port"] = free_port()
        state["dashboard_port"] = free_port()
        while state["dashboard_port"] == state["api_port"]:
            state["dashboard_port"] = free_port()
        if history:
            subprocess.run(
                [
                    str(python),
                    str(Path(__file__).with_name("seed_history.py")),
                    str(run_dir / "portfolio.sqlite"),
                ],
                env=environment(run_dir),
                cwd=ROOT,
                check=True,
            )
        config = vite_config(run_dir, state["api_port"])
        commands = [
            (
                "api",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "api_main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(state["api_port"]),
                    "--no-access-log",
                ],
            ),
            (
                "dashboard",
                [
                    "node",
                    str(vite),
                    "--config",
                    str(config),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(state["dashboard_port"]),
                    "--strictPort",
                ],
            ),
        ]
        for name, command in commands:
            with (run_dir / f"{name}.log").open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=run_dir if name == "dashboard" else ROOT,
                    env=environment(run_dir),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            state["processes"].append(
                {"name": name, "pid": process.pid, "stamp": stamp(process.pid)}
            )
            (run_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n")
        deadline = time.monotonic() + 30
        while True:
            try:
                doctor(state)
                break
            except (OSError, RuntimeError, ValueError, StopIteration):
                if (
                    any(not stamp(item["pid"]) for item in state["processes"])
                    or time.monotonic() > deadline
                ):
                    raise RuntimeError(
                        "Fixture services failed readiness; see retained logs."
                    ) from None
                time.sleep(0.3)
        print(f"DASHBOARD_URL=http://127.0.0.1:{state['dashboard_port']}")
        print("Ready: fixture reads, fake execution, blocked safeguards.")
    except Exception as error:
        (evidence_dir / "launch-failure.txt").write_text(
            f"BLOCKED: {type(error).__name__}: {error}\n"
            "Browser verification was not run.\n"
        )
        cleanup(state)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Isolated fixture-only dashboard verification."
    )
    commands = parser.add_subparsers(dest="action", required=True)
    launch = commands.add_parser("start")
    launch.add_argument("--history", action="store_true")
    for action in ("doctor", "cleanup"):
        commands.add_parser(action).add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if args.action == "start":
        start(args.history)
    elif args.action == "doctor":
        doctor(load(args.run_dir))
        print(
            "Doctor passed: processes, port ownership, API, proxy, blocked safeguards."
        )
    else:
        cleanup(load(args.run_dir))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

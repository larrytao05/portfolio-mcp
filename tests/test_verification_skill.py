import asyncio
import importlib.util
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_mcp.api import create_app
from portfolio_mcp.fixtures import FixturePortfolioProvider

SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/verify/scripts"


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load_helper("verify_dashboard")
seeder = load_helper("seed_history")


def state_for(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    evidence = tmp_path / "evidence"
    monkeypatch.setattr(helper, "RUNS", runs)
    monkeypatch.setattr(helper, "EVIDENCE", evidence)
    run = runs / "run-example"
    run.mkdir()
    proof = evidence / run.name
    proof.mkdir(parents=True)
    state = {
        "root": str(helper.ROOT),
        "run_dir": str(run),
        "evidence_dir": str(proof),
        "mode": "fixture",
        "api_port": 18001,
        "dashboard_port": 18002,
        "processes": [],
    }
    (run / "state.json").write_text(json.dumps(state))
    return run, proof, state


def test_launcher_environment_uses_allowlist_and_fixed_fixture_modes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PRIVATE_VALUE", "synthetic-test-value")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/does-not-exist")
    monkeypatch.setenv("PORTFOLIO_PROVIDER", "unsupported-test-mode")
    env = helper.environment(tmp_path)
    assert "PRIVATE_VALUE" not in env
    assert "NODE_OPTIONS" not in env
    assert env["PORTFOLIO_PROVIDER"] == "fixture"
    assert env["MARKET_DATA_PROVIDER"] == "fixture"
    assert env["EXECUTION_PROVIDER"] == "fixture"
    assert env["PORTFOLIO_DATABASE_URL"] == f"sqlite:///{tmp_path}/portfolio.sqlite"


def test_generated_vite_config_loads_with_isolated_environment_and_proxy(tmp_path):
    config = helper.vite_config(tmp_path, 18001)
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "const {default:c}=await import(process.argv[1]);"
            "console.log(JSON.stringify({root:c.root,envDir:c.envDir,"
            "proxy:c.server.proxy,plugins:c.plugins.length}));",
            config.as_uri(),
        ],
        cwd=tmp_path,
        env=helper.environment(tmp_path),
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(result.stdout)
    assert loaded["envDir"] == str(tmp_path)
    assert loaded["root"] == str(helper.ROOT / "dashboard")
    assert loaded["proxy"] == {"/api": "http://127.0.0.1:18001"}
    assert loaded["plugins"] == 1


def test_cleanup_preserves_proof_and_logs(tmp_path, monkeypatch):
    run, proof, _ = state_for(tmp_path, monkeypatch)
    (proof / "verification-summary.md").write_text("Browser check: BLOCKED\n")
    (run / "portfolio.sqlite").write_text("synthetic scratch database")
    (run / "api.log").write_text("fixture log\n")
    helper.cleanup(helper.load(run))
    assert not run.exists()
    assert (proof / "verification-summary.md").read_text() == "Browser check: BLOCKED\n"
    assert (proof / "api.log").read_text() == "fixture log\n"


def test_cleanup_rejects_arbitrary_paths_and_mismatched_evidence(tmp_path, monkeypatch):
    run, proof, state = state_for(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="RUN_DIR"):
        helper.load(tmp_path)
    state["evidence_dir"] = str(tmp_path / "unrelated")
    (run / "state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="checkout"):
        helper.load(run)
    assert run.exists() and proof.exists()


def test_stop_never_signals_a_reused_process_identity(monkeypatch):
    monkeypatch.setattr(helper, "stamp", lambda pid: "new creation time")
    signals = []
    monkeypatch.setattr(helper.os, "killpg", lambda pid, signal: signals.append(pid))
    with pytest.raises(RuntimeError, match="identity changed"):
        helper.stop({"processes": [{"pid": 999999, "stamp": "old creation time"}]})
    assert not signals


def test_failed_port_binding_leaves_evidence_and_no_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(helper, "EVIDENCE", tmp_path / "evidence")

    def blocked_port():
        raise PermissionError("Synthetic port-binding denial")

    monkeypatch.setattr(helper, "free_port", blocked_port)
    helper_path = helper.ROOT / ".venv/bin/python"
    assert helper_path.is_file()
    with pytest.raises(PermissionError):
        helper.start(history=False)
    assert list((tmp_path / "runs").iterdir()) == []
    failures = list((tmp_path / "evidence").glob("*/launch-failure.txt"))
    assert len(failures) == 1
    assert "BLOCKED" in failures[0].read_text()
    assert "Browser verification was not run" in failures[0].read_text()


def test_seed_history_has_real_persistence_gaps_and_partial_coverage(tmp_path):
    path = tmp_path / "fixture.sqlite"
    asyncio.run(seeder.seed(path))
    app = create_app(FixturePortfolioProvider(), database_url=f"sqlite:///{path}")
    with TestClient(app) as client:
        points = client.get("/api/overview/history").json()["history"]
        assert len(points) == 43
        dates = [date.fromisoformat(point["date"]) for point in points]
        assert max((right - left).days for left, right in zip(dates, dates[1:])) == 3
        assert any(not point["is_complete"] for point in points)
        assert all(point["accounts_total"] == 2 for point in points)
        assert len(client.get("/api/accounts").json()["accounts"]) == 2
        assert client.post("/api/refresh").json()["refresh"]["status"] == "success"
        assert len(client.get("/api/overview/history").json()["history"]) == 44
        accounts = client.get("/api/accounts").json()["accounts"]
        assert all(
            any(
                position["symbol"] == "VTI"
                for position in client.get(f"/api/accounts/{account['id']}").json()[
                    "account"
                ]["positions"]
            )
            for account in accounts
        )
    with pytest.raises(ValueError, match="new database"):
        asyncio.run(seeder.seed(path))

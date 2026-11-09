"""Validate and publish Oracle-only historical qualification to GitHub.

This service has no wallet/API credentials and cannot submit orders. A
repository-scoped SSH deploy key publishes only the qualification JSON plus
its SHA-256 integrity file; GitHub Actions remains the only order executor.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone

from .strategy_qualification import qualification

STATE = Path(os.environ.get("AUDACEFI_STATE_DIR", "/var/lib/autotrader/audacefi"))
REPORT = Path(os.environ.get("AUDACEFI_HISTORY_REPORT",
                             str(STATE / "audacefi-historical-qualification.json")))
KEY = Path(os.environ.get("AUDACEFI_GITHUB_DEPLOY_KEY",
                          "/var/lib/autotrader/.ssh/audacefi-research"))
KNOWN_HOSTS = Path(os.environ.get("AUDACEFI_GITHUB_KNOWN_HOSTS",
                                 "/var/lib/autotrader/.ssh/known_hosts"))
REPOSITORY = "git@github.com:PkLavc/Autotrader.git"
TARGET = "integrations/audacefi/strategy-qualification.json"


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": (f"ssh -i {KEY} -o IdentitiesOnly=yes -o BatchMode=yes "
                               f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={KNOWN_HOSTS}")}
    result = subprocess.run(args, cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=120)
    return result.stdout.strip()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".qualification-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish(report_path: Path = REPORT, state: Path = STATE) -> dict:
    report_bytes = report_path.read_bytes()
    if len(report_bytes) > 25_000_000:
        raise ValueError("ORACLE_REPORT_TOO_LARGE")
    report = json.loads(report_bytes)
    if (not isinstance(report, dict) or report.get("readOnly") is not True or
            report.get("liveExecutionAuthorized") is not False or
            not isinstance(report.get("results"), list) or
            not isinstance(report.get("allowlistExpiresAtUtc"), str)):
        raise ValueError("ORACLE_REPORT_NOT_PUBLISHABLE")
    expires = datetime.fromisoformat(report["allowlistExpiresAtUtc"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError("ORACLE_ALLOWLIST_EXPIRED")

    run_id = str(int(hashlib.sha256(report_bytes).hexdigest()[:15], 16))
    gate = qualification(report, run_id)
    gate["researchEvidenceSha256"] = hashlib.sha256(report_bytes).hexdigest()
    gate["researchHost"] = "oracle-read-only"
    payload = (json.dumps(gate, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n"

    work = state / "publisher-checkout"
    if not work.exists():
        _run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
              REPOSITORY, str(work)])
        _run(["git", "-C", str(work), "sparse-checkout", "set", TARGET,
              TARGET + ".sha256"])
    else:
        origin = _run(["git", "-C", str(work), "remote", "get-url", "origin"])
        if origin != REPOSITORY:
            raise ValueError("ORACLE_PUBLISH_REMOTE_MISMATCH")
        _run(["git", "-C", str(work), "fetch", "--depth", "1", "origin", "main"])
        _run(["git", "-C", str(work), "checkout", "-B", "main", "origin/main"])
        _run(["git", "-C", str(work), "reset", "--hard", "origin/main"])

    target = work / TARGET
    sidecar = Path(str(target) + ".sha256")
    if target.exists() and sidecar.exists() and target.read_bytes() == payload and sidecar.read_bytes() == digest:
        return {"status": "UNCHANGED", "approvedSymbols": gate["approvedSymbols"],
                "sourceRunId": gate["sourceRunId"], "sha256": digest.decode().strip()}
    _write_atomic(target, payload)
    _write_atomic(sidecar, digest)
    _run(["git", "-C", str(work), "config", "user.name", "Oracle AudaceFi Research"])
    _run(["git", "-C", str(work), "config", "user.email",
          "autotrader-research@users.noreply.github.com"])
    _run(["git", "-C", str(work), "add", TARGET, TARGET + ".sha256"])
    _run(["git", "-C", str(work), "commit", "-m",
          "chore(audacefi): publish verified Oracle qualification"])
    _run(["git", "-C", str(work), "push", "origin", "HEAD:main"])
    return {"status": "PUBLISHED", "approvedSymbols": gate["approvedSymbols"],
            "sourceRunId": gate["sourceRunId"], "sha256": digest.decode().strip()}


def main() -> int:
    result = publish()
    print("AUDACEFI_ORACLE_PUBLISH=" + json.dumps(result, sort_keys=True,
                                                   separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

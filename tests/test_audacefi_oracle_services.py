from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_oracle_research_and_kronos_share_one_persistent_cpu_lock():
    research = (ROOT / "deploy/oracle/audacefi-research.service").read_text()
    kronos = (ROOT / "deploy/oracle/audacefi-kronos-shadow.service").read_text()
    lock = "/var/lib/autotrader/audacefi/service.lock"
    assert f"flock -w 3600 {lock}" in research
    assert f"flock -w 3600 {lock}" in kronos
    runner = (ROOT / "deploy/oracle/audacefi-research-run.sh").read_text()
    assert "git pull --ff-only origin main" in runner
    assert "-m integrations.audacefi.history_job" in runner
    assert "-m integrations.audacefi.oracle_publish" in runner


def test_oracle_jobs_keep_cpu_memory_reserves_and_staggered_timers():
    research = (ROOT / "deploy/oracle/audacefi-research.service").read_text()
    kronos = (ROOT / "deploy/oracle/audacefi-kronos-shadow.service").read_text()
    research_timer = (ROOT / "deploy/oracle/audacefi-research.timer").read_text()
    kronos_timer = (ROOT / "deploy/oracle/audacefi-kronos-shadow.timer").read_text()
    assert "CPUQuota=35%" in research and "MemoryMax=1536M" in research
    assert "CPUQuota=25%" in kronos and "MemoryMax=3G" in kronos
    assert "00,06,12,18:00:00 UTC" in research_timer
    assert "03,09,15,21:00:00 UTC" in kronos_timer

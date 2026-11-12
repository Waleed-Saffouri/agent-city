"""Quick checks that the scoring and the security scanner behave. Run: python -m pytest -q"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN", "none")

from agents.job_scout import score_job  # noqa: E402


def job(title, company="Acme", locs=("New York, NY",), days=1, **kw):
    return {"title": title, "company_name": company, "locations": list(locs),
            "date_posted": time.time() - days * 86400, **kw}


def test_good_fit_scores_high():
    s, reasons = score_job(job("Data Analyst"), "new_grad")
    assert s >= 60 and "preferred location" in reasons


def test_defense_company_blocked():
    assert score_job(job("Data Analyst", company="Lockheed Martin"))[0] == 0


def test_senior_blocked():
    assert score_job(job("Senior Data Scientist"))[0] == 0


def test_off_topic_penalized():
    good, _ = score_job(job("Data Analyst"))
    bad, _ = score_job(job("Data Analyst - Transplant Nephrology"))
    assert bad < good - 20


def test_summer_internship_penalized():
    full, _ = score_job(job("Data Analyst"), "new_grad")
    summer, _ = score_job(job("Data Analyst Intern"), "internships")
    assert summer < full


def test_sentinel_finds_planted_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_TARGETS", str(tmp_path))
    proj = tmp_path / "demo"
    proj.mkdir()
    (proj / "bot.py").write_text('KEY = "sk-ant-api03-' + "Q" * 40 + '"\n')
    subprocess.run(["git", "init", "-q", str(proj)])
    import importlib, config, db
    importlib.reload(config)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db", raising=False)
    db.init()
    from agents import sentinel
    importlib.reload(sentinel)
    s = sentinel.Sentinel()
    assert s.scan_secrets(proj) >= 1

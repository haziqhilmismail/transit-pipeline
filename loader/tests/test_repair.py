import hashlib
import io
import os
import shutil
import tarfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from transit_loader import repair
from transit_loader.cli import main, repair_day
from transit_loader.feeds import file_sha256
from transit_loader.repair import Malformed, Outcome, RepairResult, member_names, plan_repair, write_merged

DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 21, 1, tzinfo=UTC)
FEED = "prasarana-rapid-bus-kl"
KTMB_DAY = Path(os.environ.get("TRANSIT_SAMPLE_DAY", "/home/haziq/harith-proj/transit/data/realtime/ktmb/2026-11-25.tar.xz"))


def at(s: int) -> str:
    return (datetime(2026, 11, 20, tzinfo=UTC) + timedelta(seconds=s)).strftime("%Y%m%dT%H%M%SZ.pb")


A_NAMES = [at(s) for s in range(0, 86400, 30) if not 36000 < s < 39600]
B_NAMES = [at(s) for s in range(10, 86400, 30)]


def write_day(path: Path, names: list[str], tag: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz", format=tarfile.USTAR_FORMAT) as tar:
        for name in names:
            payload = f"{tag}:{name}".encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    settled = (NOW - timedelta(hours=1)).timestamp()
    os.utime(path, (settled, settled))
    return path


def payloads(path: Path) -> list[bytes]:
    with tarfile.open(path, "r|xz") as tar:
        return [tar.extractfile(m).read() for m in tar]


def test_plan_fills_b_members_inside_a_gap_and_away_from_a_members():
    plan = plan_repair(A_NAMES, B_NAMES, DAY)

    assert (len(plan.fill), plan.fill[0], plan.fill[-1]) == (116, "20261120T100110Z.pb", "20261120T105840Z.pb")
    assert (plan.gap_s_before, plan.gap_s_after) == (3600, 0)


def test_plan_treats_day_edges_as_gap_bounds_but_not_as_members():
    plan = plan_repair([at(1800), at(1830)], [at(20), at(1770)], DAY)

    assert plan.fill == ("20261120T000020Z.pb",)


def test_plan_keeps_duplicate_names_from_both_collectors():
    plan = plan_repair([at(0), at(30), at(30), at(700)], [at(10), at(300), at(300), at(650)], DAY)

    assert plan.fill == ("20261120T000500Z.pb", "20261120T000500Z.pb")


def test_planning_again_on_the_merged_day_fills_nothing():
    b = [at(s) for s in range(10, 86400, 30) if not 37200 <= s < 38400]
    plan = plan_repair(A_NAMES, b, DAY)

    again = plan_repair(sorted(A_NAMES + list(plan.fill)), b, DAY)

    assert (len(plan.fill), plan.gap_s_after) == (76, 1230)
    assert (again.fill, again.gap_s_before) == ((), 1230)


@pytest.mark.parametrize("names", [[at(60), at(30)], ["latest.pb"], ["20261121T000010Z.pb"]])
def test_plan_rejects_a_day_it_must_not_rewrite(names):
    with pytest.raises(Malformed):
        plan_repair(names, B_NAMES, DAY)


@pytest.mark.skipif(not KTMB_DAY.is_file(), reason="sample day not found (set TRANSIT_SAMPLE_DAY)")
def test_merge_restores_a_cut_hour_of_a_real_day_with_duplicate_names(tmp_path):
    original = shutil.copyfile(KTMB_DAY, tmp_path / "b.tar.xz")
    a = tmp_path / "a.tar.xz"
    with tarfile.open(original, "r|xz") as src, tarfile.open(a, "w:xz", format=tarfile.USTAR_FORMAT) as dst:
        for m in src:
            if not "20261125T033000Z.pb" <= m.name < "20261125T043000Z.pb":
                dst.addfile(m, src.extractfile(m))
    plan = plan_repair(member_names(a), member_names(original), date(2026, 11, 25))

    write_merged(a, original, plan.fill, tmp_path / "merged.tar.xz", tmp_path)

    merged = member_names(tmp_path / "merged.tar.xz")
    assert merged.count("20261125T035523Z.pb") == 2
    assert merged == [n for n in member_names(original) if n in merged]
    assert (len(member_names(original)), len(member_names(a)), len(plan.fill), len(merged)) == (1874, 1754, 118, 1872)


@pytest.fixture
def archive(tmp_path):
    a = write_day(tmp_path / "data" / "realtime" / FEED / f"{DAY}.tar.xz", A_NAMES, "a")
    b = write_day(tmp_path / "b" / "realtime" / FEED / f"{DAY}.tar.xz", B_NAMES, "b")
    return tmp_path, a, b


def run(conn, tmp_path, b: Path | None, *, now=NOW, day=DAY, dry_run=False) -> RepairResult | None:
    state = None if dry_run else tmp_path / "state"
    return repair_day(conn, tmp_path / "data", FEED, day, lambda feed, d: b, now=now, state=state)


def counts(result: RepairResult) -> tuple:
    return result.outcome, result.members_added, result.gap_s_before, result.gap_s_after


def ledger(conn) -> list[tuple]:
    return conn.execute("select archive_date, outcome, members_added from bronze.rt_repair order by 1").fetchall()


@pytest.mark.db
def test_repair_fills_the_gap_once_and_keeps_a_s_original(conn, archive):
    tmp_path, a, b = archive
    original = a.read_bytes()

    result = run(conn, tmp_path, b)
    rerun = run(conn, tmp_path, b)

    assert counts(result) == (Outcome.REPAIRED, 116, 3600, 0)
    assert (result.a_sha256, result.b_sha256, result.merged_sha256) == (
        hashlib.sha256(original).hexdigest(), file_sha256(b), file_sha256(a)
    )
    assert conn.execute("select a_sha256, b_sha256, merged_sha256 from bronze.rt_repair").fetchone() == (
        result.a_sha256, result.b_sha256, result.merged_sha256
    )
    assert rerun is None
    assert len(member_names(a)) == len(A_NAMES) + 116
    assert payloads(a)[1200:1203] == [b"a:20261120T100000Z.pb", b"b:20261120T100110Z.pb", b"b:20261120T100140Z.pb"]
    assert (tmp_path / "state" / "replaced" / FEED / f"{DAY}.tar.xz").read_bytes() == original
    assert ledger(conn) == [(DAY, "repaired", 116)]
    assert sorted(p.name for p in a.parent.iterdir()) == [f"{DAY}.tar.xz"]


@pytest.mark.db
def test_dry_run_writes_nothing(conn, archive):
    tmp_path, a, b = archive
    original = a.read_bytes()

    result = run(conn, tmp_path, b, dry_run=True)

    assert counts(result) == (Outcome.REPAIRED, 116, 3600, 0)
    assert a.read_bytes() == original
    assert ledger(conn) == []


@pytest.mark.db
def test_a_whole_missing_day_is_copied_from_b(conn, archive):
    tmp_path, a, b = archive
    a.unlink()
    write_day(b, [at(0), *B_NAMES], "b")

    result = run(conn, tmp_path, b)

    assert counts(result) == (Outcome.COPIED, len(B_NAMES) + 1, 86400, 0)
    assert a.read_bytes() == b.read_bytes()
    assert ledger(conn) == [(DAY, "copied", len(B_NAMES) + 1)]


@pytest.mark.db
@pytest.mark.parametrize(
    ("a_names", "outcome"),
    [
        (B_NAMES, Outcome.CLEAN),
        (A_NAMES, Outcome.NO_FILL),
        ([at(60), at(30)], Outcome.SKIPPED_MALFORMED),
        (None, Outcome.SKIPPED_MALFORMED),
    ],
)
def test_days_with_nothing_to_fill_are_final_without_writing(conn, tmp_path, a_names, outcome):
    a = write_day(tmp_path / "data" / "realtime" / FEED / f"{DAY}.tar.xz", a_names or A_NAMES, "a")
    if a_names is None:
        a.write_bytes(a.read_bytes()[:500])
        os.utime(a, ((NOW - timedelta(hours=1)).timestamp(),) * 2)
    b_blind = write_day(tmp_path / "b.tar.xz", [at(s) for s in range(0, 36000, 30)], "b")
    original = a.read_bytes()

    assert run(conn, tmp_path, b_blind).outcome == outcome
    assert run(conn, tmp_path, b_blind) is None
    assert a.read_bytes() == original
    assert ledger(conn) == [(DAY, str(outcome), 0)]


@pytest.mark.db
def test_b_missing_waits_until_the_lookback_runs_out(conn, archive):
    tmp_path, _, _ = archive

    waiting = run(conn, tmp_path, None, now=datetime(2026, 11, 27, 1, tzinfo=UTC))
    unfillable = run(conn, tmp_path, None, now=datetime(2026, 11, 28, 1, tzinfo=UTC))

    assert (waiting.outcome, unfillable.outcome) == (Outcome.WAITING, Outcome.UNFILLABLE)
    assert ledger(conn) == [(DAY, "unfillable", 0)]


def fail_on_target(real_replace, *, after: bool):
    def fake(src, dst):
        if Path(dst).parent.parent.name == "realtime":
            if after:
                real_replace(src, dst)
            raise OSError("power cut")
        real_replace(src, dst)

    return fake


@pytest.mark.db
def test_a_crash_before_the_rename_leaves_a_intact_and_the_next_run_repairs(conn, archive, monkeypatch):
    tmp_path, a, b = archive
    original = a.read_bytes()
    monkeypatch.setattr(repair.os, "replace", fail_on_target(os.replace, after=False))

    with pytest.raises(OSError):
        run(conn, tmp_path, b)

    assert a.read_bytes() == original
    assert sorted(p.name for p in a.parent.iterdir()) == [f"{DAY}.tar.xz"]
    assert ledger(conn) == [(DAY, "repairing", 116)]
    monkeypatch.undo()
    assert run(conn, tmp_path, b).outcome == Outcome.REPAIRED
    assert len(member_names(a)) == len(A_NAMES) + 116


@pytest.mark.db
def test_a_crash_after_the_rename_is_recorded_as_repaired(conn, archive, monkeypatch):
    tmp_path, a, b = archive
    monkeypatch.setattr(repair.os, "replace", fail_on_target(os.replace, after=True))
    with pytest.raises(OSError):
        run(conn, tmp_path, b)
    merged = a.read_bytes()
    monkeypatch.undo()
    os.utime(a, ((NOW - timedelta(hours=1)).timestamp(),) * 2)

    result = run(conn, tmp_path, b)

    assert counts(result) == (Outcome.REPAIRED, 116, 3600, 0)
    assert a.read_bytes() == merged
    assert len(member_names(a)) == len(A_NAMES) + 116
    assert ledger(conn) == [(DAY, "repaired", 116)]


def open_tar(a: Path):
    (a.parent / f"{DAY}.tar").write_bytes(b"")


def packing(a: Path):
    (a.parent / f"{DAY}.tar.xz.tmp").write_bytes(b"")


def just_packed(a: Path):
    os.utime(a, ((NOW - timedelta(seconds=299)).timestamp(),) * 2)


@pytest.mark.db
@pytest.mark.parametrize("guard", [open_tar, packing, just_packed])
def test_a_day_the_hoarder_may_still_write_is_left_alone(conn, archive, guard):
    tmp_path, a, b = archive
    guard(a)
    original = a.read_bytes()

    assert run(conn, tmp_path, b) is None
    assert a.read_bytes() == original
    assert ledger(conn) == []


@pytest.mark.db
def test_today_is_left_alone(conn, archive):
    tmp_path, a, b = archive

    assert run(conn, tmp_path, b, now=datetime(2026, 11, 20, 23, tzinfo=UTC)) is None
    assert run(conn, tmp_path, b).outcome == Outcome.REPAIRED


@pytest.mark.db
def test_cli_repair_writes_only_in_mode_on(conn, warehouse_dsn, tmp_path, monkeypatch):
    day = datetime.now(UTC).date() - timedelta(days=1)
    names = [f"{day:%Y%m%d}T{hms}Z.pb" for hms in ("000010", "120000", "235950")]
    a = write_day(tmp_path / "data" / "realtime" / FEED / f"{day}.tar.xz", names[::2], "a")
    write_day(tmp_path / "b" / "realtime" / FEED / f"{day}.tar.xz", names[1:2], "b")
    for name, value in (("WAREHOUSE_DSN", warehouse_dsn), ("TRANSIT_ARCHIVE_ROOT", tmp_path / "data"),
                        ("B_DIR", tmp_path / "b"), ("TRANSIT_STATE", tmp_path / "state")):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("TRANSIT_REPAIR", raising=False)
    kept = tmp_path / "state" / "replaced" / FEED
    expired, recent = write_day(kept / "2026-10-01.tar.xz", [], "a"), write_day(kept / "2026-11-01.tar.xz", [], "a")
    for path, age in ((expired, 31), (recent, 29)):
        os.utime(path, ((datetime.now(UTC) - timedelta(days=age)).timestamp(),) * 2)

    assert main(["repair", "--mode", "off"]) == 0
    assert main(["repair", "--days", "1"]) == 0
    assert len(member_names(a)) == 2
    assert ledger(conn) == []
    assert expired.exists()

    monkeypatch.setenv("TRANSIT_REPAIR", "on")
    assert main(["repair", "--days", "1"]) == 0
    assert member_names(a) == names
    assert ledger(conn) == [(day, "repaired", 1)]
    assert sorted(p.name for p in kept.iterdir()) == ["2026-11-01.tar.xz", f"{day}.tar.xz"]


def test_cli_repair_on_without_transit_state_exits_before_touching_anything(monkeypatch, capsys):
    monkeypatch.delenv("TRANSIT_STATE", raising=False)
    monkeypatch.setenv("TRANSIT_REPAIR", "on")

    with pytest.raises(SystemExit) as exit:
        main(["repair"])

    assert exit.value.code == 2
    assert "needs TRANSIT_STATE" in capsys.readouterr().err

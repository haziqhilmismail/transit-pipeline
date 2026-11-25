from datetime import UTC, datetime

from transit_loader.feeds import Feed, list_static_versions
from transit_loader.static import parse_rows, read_version

KL = Feed.RAPID_BUS_KL


def test_list_static_versions_sorts_zips_and_ignores_state_json(archive_root):
    versions = list_static_versions(archive_root, KL)

    assert [(v.version_id, v.fetched_at) for v in versions] == [
        ("20261118T010000Z_aaaa", datetime(2026, 11, 18, 1, tzinfo=UTC)),
        ("20261119T010000Z_bbbb", datetime(2026, 11, 19, 1, tzinfo=UTC)),
    ]


def test_read_version_skips_shapes_and_macosx_entries(archive_root):
    path = archive_root / "static" / KL / "20261118T010000Z_aaaa.zip"

    assert [f.file_name for f in read_version(path)] == ["calendar.txt", "routes.txt"]


def test_parse_rows_strips_bom_header_space_and_crlf(archive_root):
    path = archive_root / "static" / KL / "20261118T010000Z_aaaa.zip"
    routes = next(f for f in read_version(path) if f.file_name == "routes.txt")

    assert list(parse_rows(routes)) == [
        {"route_id": "U3000", "route_short_name": "300"},
        {"route_id": "B1000", "route_short_name": "SUNWAY LINE"},
    ]

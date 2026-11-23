"""A restored database can contain a different company's complete sales history."""
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from pilot import recovery_binding, snapshot_profile


@pytest.mark.parametrize("image,bound,named", [
    (None, None, None), ("running:v1", None, "shop-a"),
    ("running:v1", "shop-b", "shop-a"), (None, "shop-b", "shop-a"),
    (None, None, "shop-b"),
])
def test_foreign_restore_refused(image, bound, named):
    target = {"company": "recovery", "image": image, "recovery_for": bound}
    with pytest.raises(SystemExit):
        recovery_binding(target, {"company": "shop-a"}, named)


def test_donor_binding_survives_further_backups_and_rollbacks():
    fresh = {"company": "recovery", "image": None}
    assert recovery_binding(fresh, {"company": "shop-a"}, "shop-a") == "shop-a"
    target = dict(fresh, image="running:v1", recovery_for="shop-a")
    assert recovery_binding(target, {"company": "shop-a"}, "shop-a") == "shop-a"
    own_checkpoint = {"source_instance": "recovery", "company": "shop-a"}
    assert recovery_binding(target, own_checkpoint, None) == "shop-a"
    with pytest.raises(SystemExit):
        recovery_binding(dict(target, recovery_for="shop-b"), own_checkpoint, None)


def test_archive_preserves_exact_profile_and_nested_query():
    with TemporaryDirectory() as temp:
        source = Path(temp) / "company-release.json"
        source.write_bytes(b'\xef\xbb\xbf{ "query_file": "queries/full-day.1cq" }\r\n')
        query = Path(temp) / "queries" / "full-day.1cq"
        query.parent.mkdir()
        query.write_bytes("ВЫБРАТЬ 1\r\n".encode("utf-8"))
        target = Path(temp) / "archive"
        snapshot_profile(source, target)
        assert (target / "profile.json").read_bytes() == source.read_bytes()
        assert (target / "queries" / "full-day.1cq").read_bytes() == query.read_bytes()

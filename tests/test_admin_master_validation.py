import pytest

from dotabyss_ps.admin_master import apply_overrides, patch_config
from dotabyss_ps.masterdata import MasterDataError


def data():
    schema = {"tables": {"m_events": [{"name": "id", "type": "long"}, {"name": "start_at", "type": "string"}, {"name": "end_at", "type": "string"}, {"name": "name", "type": "string"}]}}
    tables = {"m_events": [[1, "2026-10-01 00:00:00", "2026-11-01 00:00:00", "Original"]]}
    record = {"sha256": "source", "schema_sha256": "schema"}
    return schema, tables, record


@pytest.mark.parametrize("changes", [{"start_at": "not-a-date"}, {"start_at": "2027-01-01 00:00:00"}, {"id": 3}, {"start_at": 5}])
def test_invalid_overrides_are_rejected(changes):
    schema, tables, record = data()
    with pytest.raises(MasterDataError):
        patch_config({"format_version": 1, "patches": []}, tables, schema, record, "m_events", 0, changes)


def test_master_patches_are_copy_on_write_and_pinned_to_source():
    schema, tables, record = data()
    config = patch_config({"format_version": 1, "patches": []}, tables, schema, record, "m_events", 0, {"name": "Edited"})
    derived, changed = apply_overrides(tables, schema, record, config)
    assert tables["m_events"][0][-1] == "Original"
    assert derived["m_events"][0][-1] == "Edited" and changed == {"m_events": 1}
    with pytest.raises(MasterDataError):
        apply_overrides(tables, schema, {**record, "sha256": "new-source"}, config)
    tables["m_events"][0][-1] = "New original"
    with pytest.raises(MasterDataError):
        apply_overrides(tables, schema, record, config)

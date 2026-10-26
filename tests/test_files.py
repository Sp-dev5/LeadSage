import os

import pytest

from agent import files


def test_the_new_text_replaces_the_old(tmp_path):
    target = tmp_path / "icp.json"
    target.write_text("old", encoding="utf-8")
    files.write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.iterdir()) == [target]


def test_it_creates_a_missing_folder(tmp_path):
    target = tmp_path / "pages" / "a.json"
    files.write_text_atomic(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"


def test_a_failed_swap_keeps_the_old_file_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "icp.json"
    target.write_text("old", encoding="utf-8")

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        files.write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [target]

from __future__ import annotations

from pathlib import Path

from oam.persistence.filesystem_store import FilesystemStore


def test_filesystem_store_no_overwrite(tmp_path: Path):
    store = FilesystemStore(tmp_path)
    key = "binaries/ZZ/DUMMY/2015/01/x/test.bin"

    store.put_bytes(key, b"first", overwrite=False)
    store.put_bytes(key, b"second", overwrite=False)

    assert (tmp_path / key).read_bytes() == b"first"

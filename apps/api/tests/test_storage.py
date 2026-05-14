from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core import storage


class FakeObject:
    def __init__(self, data: bytes):
        self._data = data
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


def test_storage_upload_download_url_and_delete(monkeypatch):
    client = MagicMock()
    client.bucket_exists.side_effect = [False, True, True]
    client.get_object.return_value = FakeObject(b"content")
    monkeypatch.setattr(storage, "_client", client)

    storage.upload_file(b"abc", "a/b.pdf", "application/pdf")
    client.put_object.assert_called_once()

    client.presigned_get_object.return_value = "http://signed"
    assert storage.presigned_get_url("a/b.pdf") == "http://signed"
    assert storage.get_file_bytes("a/b.pdf") == b"content"
    storage.delete_object("a/b.pdf")
    client.remove_object.assert_called_once()


def test_get_minio_initializes_singleton(monkeypatch):
    instance = MagicMock()
    instance.bucket_exists.side_effect = [False, True]
    fake_minio = MagicMock(return_value=instance)
    monkeypatch.setattr(storage, "_client", None)
    monkeypatch.setattr(storage, "Minio", fake_minio)

    first = storage.get_minio()
    second = storage.get_minio()
    assert first is second
    fake_minio.assert_called_once()

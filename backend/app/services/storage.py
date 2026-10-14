"""MinIO 对象存储封装（计划书 4.3 services/storage.py、5.7 heatmap_cache）。

存 demo 原文件（Phase 3 上传）与热力图图片（Phase 2）。
上传使用流式（fput_object），避免大文件全量进内存（P1-1）。
"""
from __future__ import annotations

import io
from pathlib import Path

from minio import Minio

from app.config import settings


class Storage:
    def __init__(self) -> None:
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        self.bucket = settings.minio_bucket

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def upload_bytes(self, key: str, data: bytes, content_type: str = "image/png") -> str:
        """上传字节流，返回 key（热力图等小对象用）。"""
        self.ensure_bucket()
        self.client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return key

    def upload_file(self, key: str, path: str | Path, content_type: str = "application/octet-stream") -> str:
        """流式上传本地文件（fput_object 内部分块读，不全量进内存，P1-1）。"""
        self.ensure_bucket()
        self.client.fput_object(self.bucket, key, str(path), content_type=content_type)
        return key

    def get_bytes(self, key: str) -> bytes:
        resp = self.client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat_size(self, key: str) -> int:
        """Return an object's byte size without downloading its body."""
        return int(self.client.stat_object(self.bucket, key).size)

    def iter_bytes(self, key: str, chunk_size: int = 1024 * 1024):
        """Open immediately, then yield bounded chunks and always release the connection."""
        resp = self.client.get_object(self.bucket, key)

        def chunks():
            try:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    resp.close()
                finally:
                    resp.release_conn()

        return chunks()

    def exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> None:
        """删除对象；调用方只应传入本次生成且尚未入库的 key。"""
        self.client.remove_object(self.bucket, key)


storage = Storage()

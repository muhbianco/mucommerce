"""Object storage behind a small protocol: MinIO in production, an in-memory fake in tests.

The MinIO SDK is synchronous (urllib3), so every call runs in a worker thread; the pool has
explicit timeouts and retries only idempotent transport failures (the SDK's own Retry). The
server talks to the internal endpoint; browsers only ever see `storage_public_url`, which is
also where presigned POST uploads go (a SigV4 POST policy signs the policy, not the host).
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

import urllib3
from minio import Minio
from minio.datatypes import PostPolicy
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from app.core.config import settings
from app.core.exceptions import DomainError

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class StorageUnavailableError(DomainError):
    status_code = 503
    error_code = "storage_unavailable"
    message = "Armazenamento de arquivos indisponível no momento."


class ObjectTooLargeError(Exception):
    """The object is bigger than the caller agreed to read."""


class ObjectMissingError(Exception):
    """No object under that key (never uploaded, or already purged)."""


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    size: int
    content_type: str | None


class ObjectStorage(Protocol):
    async def presigned_post(
        self, bucket: str, key: str, *, content_type: str, max_bytes: int, expires_at: datetime
    ) -> PresignedPost: ...

    async def stat(self, bucket: str, key: str) -> ObjectInfo | None: ...

    async def read(self, bucket: str, key: str, *, max_bytes: int) -> bytes: ...

    async def put(
        self, bucket: str, key: str, data: bytes, *, content_type: str, cache_control: str
    ) -> None: ...

    async def delete(self, bucket: str, keys: list[str]) -> None: ...

    async def bucket_exists(self, bucket: str) -> bool: ...


def _public_base() -> str:
    return settings.storage_public_url.rstrip("/")


def public_object_url(bucket: str, key: str) -> str:
    """Anonymous URL of a public object (pure: no client, works with storage unconfigured)."""
    return f"{_public_base()}/{bucket}/{key}"


class MinioStorage:
    def __init__(self) -> None:
        endpoint = urlsplit(settings.storage_endpoint)
        timeout = settings.storage_timeout_seconds
        self._client = Minio(
            endpoint.netloc,
            access_key=settings.storage_access_key.get_secret_value(),
            secret_key=settings.storage_secret_key.get_secret_value(),
            secure=endpoint.scheme == "https",
            # Explicit region: no GetBucketLocation round trip, and the POST policy is scoped
            # to the region MinIO validates against.
            region=settings.storage_region,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=5.0, read=timeout),
                maxsize=10,
                retries=urllib3.Retry(
                    total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
                ),
            ),
        )

    async def presigned_post(
        self, bucket: str, key: str, *, content_type: str, max_bytes: int, expires_at: datetime
    ) -> PresignedPost:
        policy = PostPolicy(bucket, expires_at)
        policy.add_equals_condition("key", key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(1, max_bytes)
        # Pure computation (HMAC); no I/O because the region is fixed.
        form = self._client.presigned_post_policy(policy)
        return PresignedPost(
            url=f"{_public_base()}/{bucket}",
            fields={"key": key, "Content-Type": content_type, **form},
        )

    async def stat(self, bucket: str, key: str) -> ObjectInfo | None:
        def _stat() -> ObjectInfo | None:
            try:
                info = self._client.stat_object(bucket, key)
            except S3Error as exc:
                if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                    return None
                raise
            return ObjectInfo(size=int(info.size or 0), content_type=info.content_type)

        return await asyncio.to_thread(_stat)

    async def read(self, bucket: str, key: str, *, max_bytes: int) -> bytes:
        def _read() -> bytes:
            try:
                response = self._client.get_object(bucket, key)
            except S3Error as exc:
                if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                    raise ObjectMissingError(key) from exc
                raise
            try:
                data = response.read(max_bytes + 1)
            finally:
                response.close()
                response.release_conn()
            if len(data) > max_bytes:
                raise ObjectTooLargeError(key)
            return bytes(data)

        return await asyncio.to_thread(_read)

    async def put(
        self, bucket: str, key: str, data: bytes, *, content_type: str, cache_control: str
    ) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
            metadata={"Cache-Control": cache_control},
        )

    async def delete(self, bucket: str, keys: list[str]) -> None:
        def _delete() -> None:
            errors = list(self._client.remove_objects(bucket, [DeleteObject(k) for k in keys]))
            if errors:
                raise RuntimeError(f"{len(errors)} objects not deleted: {errors[0]}")

        if keys:
            await asyncio.to_thread(_delete)

    async def bucket_exists(self, bucket: str) -> bool:
        return await asyncio.to_thread(self._client.bucket_exists, bucket)


@dataclass
class _StoredObject:
    data: bytes
    content_type: str
    cache_control: str = ""


@dataclass
class FakeStorage:
    """In-memory storage for tests; `objects[(bucket, key)]`."""

    public_base: str = "https://storage.test"
    objects: dict[tuple[str, str], _StoredObject] = field(default_factory=dict)
    posts: list[tuple[str, str, int]] = field(default_factory=list)

    async def presigned_post(
        self, bucket: str, key: str, *, content_type: str, max_bytes: int, expires_at: datetime
    ) -> PresignedPost:
        del expires_at
        self.posts.append((bucket, key, max_bytes))
        return PresignedPost(
            url=f"{self.public_base}/{bucket}",
            fields={"key": key, "Content-Type": content_type, "policy": "fake"},
        )

    def upload(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        """What the browser does with the presigned POST."""
        self.objects[(bucket, key)] = _StoredObject(data, content_type)

    async def stat(self, bucket: str, key: str) -> ObjectInfo | None:
        stored = self.objects.get((bucket, key))
        return ObjectInfo(len(stored.data), stored.content_type) if stored else None

    async def read(self, bucket: str, key: str, *, max_bytes: int) -> bytes:
        stored = self.objects.get((bucket, key))
        if stored is None:
            raise ObjectMissingError(key)
        data = stored.data
        if len(data) > max_bytes:
            raise ObjectTooLargeError(key)
        return data

    async def put(
        self, bucket: str, key: str, data: bytes, *, content_type: str, cache_control: str
    ) -> None:
        self.objects[(bucket, key)] = _StoredObject(data, content_type, cache_control)

    async def delete(self, bucket: str, keys: list[str]) -> None:
        for key in keys:
            self.objects.pop((bucket, key), None)

    async def bucket_exists(self, bucket: str) -> bool:
        del bucket
        return True


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    """Process-wide client (the SDK pools connections). 503 when storage is not configured."""
    global _storage
    if _storage is None:
        if not settings.storage_configured:
            raise StorageUnavailableError()
        _storage = MinioStorage()
    return _storage

"""Document storage: S3/MinIO → GridFS → local directory fallback chain."""
import logging
import os
from typing import Optional, Tuple

from app.core.config import settings

log = logging.getLogger("pharmaos.storage")


class Storage:
    def __init__(self):
        self._s3 = None
        self._mode = None

    async def init(self):
        mode = settings.STORAGE_BACKEND
        if mode in ("auto", "s3") and settings.S3_ENDPOINT and settings.AWS_ACCESS_KEY_ID:
            try:
                import boto3

                self._s3 = boto3.client(
                    "s3",
                    endpoint_url=settings.S3_ENDPOINT,
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                )
                buckets = await asyncio_s3_list(self._s3)
                if settings.S3_BUCKET not in buckets:
                    self._s3.create_bucket(Bucket=settings.S3_BUCKET)
                self._mode = "s3"
            except Exception as e:
                log.warning("S3 unavailable (%s), falling back", e)
                self._s3 = None
        if self._mode is None and db_gridfs_available():
            self._mode = "gridfs"
        if self._mode is None:
            self._mode = "local"
        self._ensure_local_dir()
        log.info("storage mode=%s", self._mode)

    def _ensure_local_dir(self):
        """The local fallback must never fail just because the directory is
        missing (fresh checkout / CI / first use before init())."""
        try:
            os.makedirs(settings.LOCAL_UPLOAD_DIR, exist_ok=True)
        except OSError as e:
            log.warning("could not create upload dir %s: %s",
                        settings.LOCAL_UPLOAD_DIR, e)

    @property
    def mode(self) -> str:
        return self._mode or "local"

    async def put(self, content: bytes, filename: str, content_type: str = "") -> Tuple[str, str]:
        """Store bytes → returns (ref, storage_mode)."""
        if self._mode is None:  # not init()'d (e.g. service-level tests)
            await self.init()
        if self._mode == "s3":
            key = filename
            await asyncio_s3_put(self._s3, settings.S3_BUCKET, key, content, content_type)
            return f"s3://{settings.S3_BUCKET}/{key}", "s3"
        if self._mode == "gridfs":
            from app.core.database import db

            fid = await db.gridfs.upload_from_stream(filename, content)
            return f"gridfs://{fid}", "gridfs"
        path = self._local_path(filename)
        self._ensure_local_dir()
        with open(path, "wb") as f:
            f.write(content)
        return f"local://{path}", "local"

    @staticmethod
    def _local_path(filename: str) -> str:
        """Flatten client-supplied names: no path separators (traversal), no
        collisions between same-named files from different uploads."""
        import uuid

        base = os.path.basename(str(filename or "file")) or "file"
        return os.path.join(settings.LOCAL_UPLOAD_DIR, f"{uuid.uuid4().hex}_{base}")

    async def get(self, ref: str) -> Optional[bytes]:
        if ref.startswith("s3://"):
            bucket, key = ref[5:].split("/", 1)
            return await asyncio_s3_get(self._s3, bucket, key)
        if ref.startswith("gridfs://"):
            fid = ref.split("://")[1]
            import io

            from bson import ObjectId
            from app.core.database import db

            try:
                buf = io.BytesIO()
                await db.gridfs.download_to_stream(ObjectId(fid), buf)
                return buf.getvalue()
            except Exception:
                return None
        if ref.startswith("local://"):
            path = ref[8:]
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError:
                return None
        return None


async def asyncio_s3_list(client) -> list:
    import asyncio

    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(None, client.list_buckets)
    return [b["Name"] for b in resp.get("Buckets", [])]


async def asyncio_s3_put(client, bucket: str, key: str, content: bytes, ct: str):
    import asyncio

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: client.put_object(Bucket=bucket, Key=key, Body=content,
                                        ContentType=ct or "application/octet-stream")
    )


async def asyncio_s3_get(client, bucket: str, key: str) -> bytes:
    import asyncio

    loop = asyncio.get_event_loop()
    resp = await loop.run_in_executor(
        None, lambda: client.get_object(Bucket=bucket, Key=key)
    )
    return resp["Body"].read()


def db_gridfs_available() -> bool:
    from app.core.database import db

    return db.db is not None


storage = Storage()

import asyncio

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.config import settings


class StorageError(Exception):
    pass


def _normalize_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"http://{value}"


class MinIOClient:
    def __init__(self):
        self.internal_endpoint = settings.AWS_ENDPOINT_URL or _normalize_endpoint(settings.MINIO_ENDPOINT)
        self.external_endpoint = settings.MINIO_EXTERNAL_URL if self.internal_endpoint else None
        self.access_key_id = settings.AWS_ACCESS_KEY_ID or settings.MINIO_ACCESS_KEY
        self.secret_access_key = settings.AWS_SECRET_ACCESS_KEY or settings.MINIO_SECRET_KEY
        self.client = boto3.client(
            "s3",
            endpoint_url=self.internal_endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=settings.AWS_REGION,
            config=Config(signature_version="s3v4"),
            use_ssl=settings.MINIO_SECURE,
        )
        self.bucket = settings.MINIO_BUCKET
        self._external_client = None
    
    def _get_external_client(self):
        if self._external_client is None:
            self._external_client = boto3.client(
                "s3",
                endpoint_url=self.external_endpoint,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=settings.AWS_REGION,
                config=Config(signature_version="s3v4"),
                use_ssl=settings.MINIO_SECURE,
            )
        return self._external_client
    
    def ensure_bucket_exists(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except ClientError as e:
                raise StorageError(f"Failed to create bucket: {e}")

    def health_check(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as e:
            raise StorageError(f"Bucket health check failed: {e}")
    
    def upload_pdf(self, pdf_bytes: bytes, s3_key: str) -> str:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=pdf_bytes,
                ContentType="application/pdf"
            )
            return s3_key
        except ClientError as e:
            raise StorageError(f"Failed to upload PDF: {e}")
    
    def download_pdf(self, s3_key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=s3_key)
            return response['Body'].read()
        except ClientError as e:
            raise StorageError(f"Failed to download PDF: {e}")
    
    def get_presigned_url(self, s3_key: str, expires_seconds: int = 900) -> str:
        try:
            self.client.head_object(Bucket=self.bucket, Key=s3_key)
        except ClientError:
            raise StorageError(f"Key not found: {s3_key}")
        
        try:
            external_client = self._get_external_client()
            presigned_url = external_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': s3_key},
                ExpiresIn=expires_seconds
            )
            return presigned_url
        except ClientError as e:
            raise StorageError(f"Failed to generate presigned URL: {e}")
    
    def delete_file(self, s3_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=s3_key)
        except ClientError as e:
            raise StorageError(f"Failed to delete file: {e}")

    def delete_file_if_exists(self, s3_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=s3_key)
        except ClientError as e:
            error_code = str((e.response or {}).get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return
            raise StorageError(f"Failed to delete file: {e}")

    async def async_upload_pdf(self, pdf_bytes: bytes, s3_key: str) -> str:
        return await asyncio.to_thread(self.upload_pdf, pdf_bytes, s3_key)

    async def async_download_pdf(self, s3_key: str) -> bytes:
        return await asyncio.to_thread(self.download_pdf, s3_key)

    async def async_get_presigned_url(self, s3_key: str, expires_seconds: int = 900) -> str:
        return await asyncio.to_thread(self.get_presigned_url, s3_key, expires_seconds)

    async def async_delete_file(self, s3_key: str) -> None:
        await asyncio.to_thread(self.delete_file, s3_key)

    async def async_delete_file_if_exists(self, s3_key: str) -> None:
        await asyncio.to_thread(self.delete_file_if_exists, s3_key)

    async def async_ensure_bucket_exists(self) -> None:
        await asyncio.to_thread(self.ensure_bucket_exists)

    async def async_health_check(self) -> bool:
        return await asyncio.to_thread(self.health_check)


minio_client = MinIOClient()

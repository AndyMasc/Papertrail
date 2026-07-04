import boto3
from botocore.config import Config
from django.conf import settings

R2_PAPERTRAIL_STORAGE_ACCOUNT_ID = settings.R2_PAPERTRAIL_STORAGE_ACCOUNT_ID
R2_STORAGE_BUCKET_NAME = settings.R2_STORAGE_BUCKET_NAME

s3 = boto3.client(
    service_name="s3",
    endpoint_url=settings.R2_S3_ENDPOINT_URL,
    aws_access_key_id=settings.R2_ACCESS_KEY_ID,
    aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

def generate_write_presigned_url(key, content_type):
    return s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": R2_STORAGE_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
    )

def generate_read_presigned_url(key):
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": R2_STORAGE_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=900,
    )
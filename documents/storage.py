import uuid
from pathlib import Path
from .models import Document_data
import boto3
from botocore.config import Config
from django.conf import settings

R2_PAPERTRAIL_STORAGE_ACCOUNT_ID = settings.R2_PAPERTRAIL_STORAGE_ACCOUNT_ID
R2_STORAGE_BUCKET_NAME = settings.R2_STORAGE_BUCKET_NAME

s3 = boto3.client( # s3 client for R2 storage configuration
    service_name="s3",
    endpoint_url=settings.R2_S3_ENDPOINT_URL,
    aws_access_key_id=settings.R2_ACCESS_KEY_ID,
    aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    region_name="auto",
    config=Config(signature_version="s3v4"),
)

def generate_write_presigned_url(key, content_type): # generates a presigned URL for writing to R2 storage
    return s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": R2_STORAGE_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
    )

def generate_read_presigned_url(key): # generates a presigned URL for reading from R2 storage
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": R2_STORAGE_BUCKET_NAME,
            "Key": key,
        },
        ExpiresIn=900,
    )

def initiate_r2_upload(user, filename, content_type, record_id=None, notes=None): # initiates an R2 upload by generating a file-specific presigned URL for writing
    if not content_type:
        content_type = "application/octet-stream"
        
    if not filename:
        raise ValueError("Filename is required")

    title = Path(filename).stem.replace('_', ' ').replace('-', ' ').title()
    extension = Path(filename).suffix
    key = f"users/{user.id}/{uuid.uuid4()}{extension}"

    document = Document_data.objects.create(
        user=user, 
        filepath=key,
        associated_record_id=record_id,
        is_main=(record_id is None),
        title=title,
        notes=notes
    )
    
    upload_url = generate_write_presigned_url(key, content_type)
    
    return {
        "upload_url": upload_url,
        "key": key,
        "document_id": document.id
    }
import uuid
from pathlib import Path
from .models import DocumentData
import boto3
from botocore.config import Config
from django.conf import settings
import hashlib

R2_PAPERTRAIL_STORAGE_ACCOUNT_ID = settings.R2_PAPERTRAIL_STORAGE_ACCOUNT_ID
R2_STORAGE_BUCKET_NAME = settings.R2_STORAGE_BUCKET_NAME

# s3 client for R2 storage
s3 = boto3.client( 
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

def initiate_r2_upload(user, file_obj, content_type, file_hash, record_id=None, notes=None, force_upload=False):
    
    if not force_upload:
        existing_doc = DocumentData.objects.filter(user=user, file_hash=file_hash).first()
        if existing_doc:
            return {
                "status": "duplicate",
                "document_id": existing_doc.id,
                "record_id": existing_doc.associated_record_id 
            }
    else:
        salt = f"-forced-{uuid.uuid4().hex}"
        mutated_string = file_hash + salt
        file_hash = hashlib.sha256(mutated_string.encode('utf-8')).hexdigest()

    safe_filename = Path(file_obj.name).name
    title = Path(safe_filename).stem.replace('_', ' ').replace('-', ' ').title()
    extension = Path(safe_filename).suffix.lower()
    key = f"users/{user.id}/{uuid.uuid4()}{extension}"

    document = DocumentData(
        user=user, 
        filepath=key,
        associated_record_id=record_id,
        is_main=(record_id is None),
        title=title,
        notes=notes,
        file_hash=file_hash,
    )
    document.save()
    
    upload_url = generate_write_presigned_url(key, content_type)
    
    return {
        "status": "upload_url",
        "upload_url": upload_url,
        "key": key,
        "document_id": document.id
    }
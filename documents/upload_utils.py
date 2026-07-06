import uuid
from pathlib import Path
from .models import Document_data
from .storage_helpers import generate_write_presigned_url

def initiate_r2_upload(user, filename, content_type, record_id=None, notes=None):
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
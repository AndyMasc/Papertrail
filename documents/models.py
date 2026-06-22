from django.db import models
from storages.backends.gcloud import GoogleCloudStorage

# Create your models here.

storage = GoogleCloudStorage()
class Upload:
    @staticmethod
    def upload_image(file, filename):
        try:
            target_path = '/images/' + filename
            path = storage.save(target_path, file)
            return storage.url(path)
        except Exception as e:
            print("Failed to upload!")
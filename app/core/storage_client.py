from minio import Minio
from minio.error import S3Error
from core.config import settings
import io
import os

def get_storage_client():
    """Retorna cliente S3 (SeaweedFS)."""
    client = Minio(
        settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key,
        secure=settings.storage_secure
    )
    
    # Asegurar que el bucket existe
    try:
        if not client.bucket_exists(settings.storage_bucket):
            client.make_bucket(settings.storage_bucket)
    except S3Error as err:
        print(f"SeaweedFS Error: {err}")
        
    return client

def upload_file_to_storage(local_path_or_data, object_name: str, content_type: str = "application/json"):
    """
    Sube un archivo o bytes al almacenamiento.
    Compatible con SeaweedFS S3 API.
    """
    client = get_storage_client()
    
    if isinstance(local_path_or_data, str) and os.path.exists(local_path_or_data):
        # Es una ruta de archivo
        file_size = os.path.getsize(local_path_or_data)
        with open(local_path_or_data, 'rb') as f:
            client.put_object(
                settings.storage_bucket,
                object_name,
                f,
                length=file_size,
                content_type=content_type
            )
    else:
        # Son bytes
        data = local_path_or_data
        if isinstance(data, str): data = data.encode('utf-8')
        client.put_object(
            settings.storage_bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type
        )
    return f"{settings.storage_bucket}/{object_name}"

# Alias para compatibilidad temporal durante la migración
get_storage_client = get_storage_client
upload_file_to_storage = upload_file_to_storage

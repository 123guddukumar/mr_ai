import os
import re
import logging
import boto3
from botocore.config import Config
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

def upload_to_r2(local_file_path: str, r2_key: str, content_type: str = "video/mp4") -> Optional[str]:
    """
    Uploads a local file to Cloudflare R2 bucket.
    Returns the publicly accessible URL if successful, or None if failed.
    """
    if not os.path.exists(local_file_path) or os.path.getsize(local_file_path) == 0:
        logger.error(f"R2 Upload Error: Local file does not exist or is empty: {local_file_path}")
        return None
        
    access_key = settings.R2_ACCESS_KEY_ID
    secret_key = settings.R2_SECRET_ACCESS_KEY
    bucket_name = settings.R2_BUCKET_NAME
    endpoint = settings.R2_ENDPOINT
    
    if not (access_key and secret_key and bucket_name and endpoint):
        logger.warning("R2 Credentials are not configured in system settings. Skipping upload.")
        return None
        
    logger.info(f"Uploading {local_file_path} to Cloudflare R2 bucket '{bucket_name}' under key '{r2_key}'...")
    
    try:
        # Configure standard S3 client for Cloudflare R2 (auto region is required)
        s3 = boto3.client(
            service_name="s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4")
        )
        
        # Upload file with public read capability
        s3.upload_file(
            local_file_path,
            bucket_name,
            r2_key,
            ExtraArgs={
                "ContentType": content_type
            }
        )
        
        logger.info(f"R2 Upload successful: key='{r2_key}'")
        
        # Construct the public URL
        public_url = ""
        if settings.R2_PUBLIC_URL:
            public_url = f"{settings.R2_PUBLIC_URL.rstrip('/')}/{r2_key}"
        else:
            # Fallback to dev subdomain extraction from endpoint host
            match = re.search(r'https://([^.]+)\.r2\.cloudflarestorage\.com', endpoint)
            if match:
                account_hash = match.group(1)
                public_url = f"https://pub-{account_hash}.r2.dev/{r2_key}"
            else:
                # Emergency fallback to endpoint structure
                public_url = f"{endpoint.rstrip('/')}/{bucket_name}/{r2_key}"
                
        logger.info(f"R2 Public URL: {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"Cloudflare R2 Upload Failed: {e}", exc_info=True)
        return None


def delete_from_r2(r2_key: str) -> bool:
    """
    Deletes an object from Cloudflare R2 bucket by key.
    Returns True if successful, or False if failed.
    """
    access_key = settings.R2_ACCESS_KEY_ID
    secret_key = settings.R2_SECRET_ACCESS_KEY
    bucket_name = settings.R2_BUCKET_NAME
    endpoint = settings.R2_ENDPOINT
    
    if not (access_key and secret_key and bucket_name and endpoint):
        return False
        
    logger.info(f"Deleting key '{r2_key}' from R2 bucket '{bucket_name}'...")
    try:
        s3 = boto3.client(
            service_name="s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4")
        )
        s3.delete_object(Bucket=bucket_name, Key=r2_key)
        logger.info(f"R2 key '{r2_key}' deleted successfully.")
        return True
    except Exception as e:
        logger.error(f"Cloudflare R2 deletion failed for key '{r2_key}': {e}")
        return False

"""
Audio Upload Utility - Upload audio to GCS when calls disconnect
Used by ACStreamTestWithLK.py to save audio directly to GCP Cloud Storage
"""

import os
import logging
from pathlib import Path
from google.cloud import storage
try:
    from .config import CONFIG
except Exception:
    try:
        from config import CONFIG
    except Exception:
        CONFIG = {
            "GCS_KEY_FILE": os.environ.get("GCS_KEY_FILE", "file.json"),
            "GCS_BUCKET_NAME": os.environ.get("GCS_BUCKET_NAME", "voice_bot_travbridge"),
            "GCS_FOLDER_AUDIOS": os.environ.get("GCS_FOLDER_AUDIOS", "")
        }

logger = logging.getLogger("audio_uploader")


def upload_audio_to_gcs(audio_file_path: str, audio_filename: str) -> tuple[bool, str]:
    """
    Upload audio file directly to GCS bucket
    
    Args:
        audio_file_path: Full path to local audio file
        audio_filename: Filename to use in GCS (e.g., appid_callerid_uuid.wav)
    
    Returns:
        tuple: (success: bool, gcs_path: str)
        Example: (True, "gs://voice_bot/appid_callerid_uuid.wav")
    """
    try:
        # Initialize GCS client
        key_path = CONFIG.get("GCS_KEY_FILE", "file.json")
        bucket_name = CONFIG.get("GCS_BUCKET_NAME", "voice_bot_travbridge")
        folder_audios = CONFIG.get("GCS_FOLDER_AUDIOS", "")
        
        # Check if file exists
        if not os.path.exists(audio_file_path):
            logger.error(f"Audio file not found: {audio_file_path}")
            return False, ""
        
        # Initialize storage client
        storage_client = storage.Client.from_service_account_json(key_path)
        bucket = storage_client.bucket(bucket_name)
        
        # Create GCS blob name
        gcs_blob_name = f"{folder_audios}{audio_filename}"
        blob = bucket.blob(gcs_blob_name)
        
        # Upload file
        file_size = os.path.getsize(audio_file_path)
        logger.info(f"⬆️ Uploading to GCS: {audio_filename} ({file_size / 1024:.2f} KB)")
        
        blob.upload_from_filename(audio_file_path)
        
        gcs_path = f"gs://{bucket_name}/{gcs_blob_name}"
        logger.info(f"✓ Successfully uploaded: {gcs_path}")
        # Remove local file after successful upload
        try:
            if os.path.exists(audio_file_path):
                os.remove(audio_file_path)
                logger.info(f"Local file removed after upload: {audio_file_path}")
        except Exception as e:
            logger.warning(f"Could not remove local file {audio_file_path}: {e}")

        return True, gcs_path
        
    except Exception as e:
        logger.error(f"✗ Failed to upload audio to GCS: {e}")
        return False, ""
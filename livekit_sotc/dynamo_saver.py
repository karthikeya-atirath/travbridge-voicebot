"""
dynamo_saver.py

Saves conversation data to AWS DynamoDB.
Each conversation is stored with its conversationId as the primary key,
an IST timestamp, and a dev/prod environment marker.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

from app_logger import applog
from configuration.config_env import dynamo_table_name, APP_ENV


def _now_ist() -> str:
    """Return current time in IST as ISO string."""
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).isoformat()


def _sanitise_for_dynamo(obj):
    """
    Recursively convert types that DynamoDB cannot handle:
      - float → Decimal
      - None  → removed (DynamoDB does not allow None in sets/numbers)
    """
    if isinstance(obj, dict):
        return {k: _sanitise_for_dynamo(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_sanitise_for_dynamo(i) for i in obj]
    elif isinstance(obj, float):
        return Decimal(str(obj))
    return obj


class DynamoSaver:
    """Handles writing conversation documents to DynamoDB."""

    _table = None  # lazily initialised

    @classmethod
    def _get_table(cls):
        """Return the DynamoDB Table resource (created once, reused)."""
        if cls._table is None:
            if not dynamo_table_name:
                raise RuntimeError("DYNAMODB_TABLE_NAME is not set in environment")

            session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_DEFAULT_REGION", "ap-south-1"),
            )
            dynamodb = session.resource("dynamodb")
            cls._table = dynamodb.Table(dynamo_table_name)
            applog.info(
                "[DYNAMO] Initialised table=%s region=%s",
                dynamo_table_name,
                os.getenv("AWS_DEFAULT_REGION", "ap-south-1"),
            )
        return cls._table

    @classmethod
    def save_conversation(cls, doc: Dict[str, Any]) -> bool:
        """
        Write a conversation document to DynamoDB.

        Args:
            doc: The ChatHistory.doc dictionary.

        Returns:
            True on success, False on failure.
        """
        conversation_id = doc.get("conversationId")
        if not conversation_id:
            applog.error("[DYNAMO] Missing conversationId — skipping save")
            return False

        ist_now = _now_ist()
        environment = APP_ENV  # "dev" or "prod"

        item = {
            "conversationId": conversation_id,
            "timestamp_ist": ist_now,
            "environment": environment,
            "customerId": doc.get("customerId", ""),
            "userId": doc.get("userId", ""),
            "chat_started": doc.get("chat_started", ""),
            "chat_modified": doc.get("chat_modified", ""),
            "opportunity_id": doc.get("opportunity_id", ""),
            "chat_channel": doc.get("chat_channel", ""),
            "customer_first_name": doc.get("customer_first_name", ""),
            "customer_last_name": doc.get("customer_last_name", ""),
            "customer_email": doc.get("customer_email", ""),
            "customer_phone": doc.get("customer_phone", ""),
            "conversation_data": json.dumps(doc, ensure_ascii=False, default=str),
        }

        # Sanitise (floats → Decimal, strip None values)
        item = _sanitise_for_dynamo(item)

        try:
            table = cls._get_table()
            table.put_item(Item=item)
            applog.info(
                "[DYNAMO] ✓ Saved | conversationId=%s | env=%s | table=%s",
                conversation_id,
                environment,
                dynamo_table_name,
            )
            return True

        except ClientError as e:
            applog.error(
                "[DYNAMO] ✗ ClientError saving to DynamoDB | conversationId=%s | error=%s",
                conversation_id,
                e.response["Error"]["Message"],
            )
            return False

        except Exception as e:
            applog.error(
                "[DYNAMO] ✗ Unexpected error saving to DynamoDB | conversationId=%s | error=%s",
                conversation_id,
                str(e),
            )
            return False

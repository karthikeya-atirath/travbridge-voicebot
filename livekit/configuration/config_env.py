# configuration/config_env.py

import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from constants import ELASTIC_SEARCH_URL_SAVE_CONVERSATION
from constants import OPPORTUNITY_CREATE_URL
from constants import DYNAMODB_TABLE_NAME

logger = logging.getLogger("config_env")

# Project root folder (one level above /configuration)
BASE_DIR = Path(__file__).resolve().parent.parent

#
# APP_ENV can be "dev" or "prod"
APP_ENV = os.getenv("APP_ENV", "prod").lower()

if APP_ENV == "prod":
    env_path = BASE_DIR / ".env_prod"
else:
    # normalize anything else to dev
    APP_ENV = "dev"
    env_path = BASE_DIR / ".env_dev"

# Log which environment is selected
msg_env = f"[config_env] APP_ENV={APP_ENV} -> using {env_path.name}"
print(msg_env)              # visible in terminal
logger.info(msg_env)        # visible in logs once logging is configured

# Load the chosen .env file
if env_path.exists():
    load_dotenv(env_path, override=True)  # override=True ensures .env_prod beats docker env_file injected vars
    msg_loaded = f"[config_env] Loaded env file: {env_path}"
    print(msg_loaded)
    logger.info(msg_loaded)
else:
    msg_warn = f"[config_env] WARNING: env file not found: {env_path}"
    print(msg_warn)
    logger.warning(msg_warn)

elastic_search_url =os.getenv(ELASTIC_SEARCH_URL_SAVE_CONVERSATION)
opportunity_create_url = os.getenv(OPPORTUNITY_CREATE_URL)
dynamo_table_name = os.getenv(DYNAMODB_TABLE_NAME)
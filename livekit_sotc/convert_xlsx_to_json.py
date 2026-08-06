"""
Convert Book1.xlsx to call_list.json with status tracking.
Each entry has: mobile, status (uncalled/called/failed), timestamp.
If call_list.json already exists, it preserves existing statuses.
"""

import json
import os
import logging
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

XLSX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Book2.xlsx")
JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_list.json")


def convert():
    """Read the xlsx and create/update call_list.json."""
    logger.info("Reading Excel file: %s", XLSX_PATH)
    df = pd.read_excel(XLSX_PATH)

    # The column is named "Mobile"
    if "Mobile" not in df.columns:
        raise ValueError(
            f"Expected column 'Mobile' in Excel. Found: {df.columns.tolist()}"
        )

    mobile_numbers = df["Mobile"].dropna().astype(str).str.strip().tolist()
    # Remove any non-digit entries and ensure 10-digit numbers
    mobile_numbers = [m for m in mobile_numbers if m.isdigit() and len(m) == 10]
    logger.info("Found %d valid 10-digit mobile numbers in Excel.", len(mobile_numbers))

    # Load existing JSON if present to preserve statuses
    existing_data = {}
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, "r") as f:
                existing_list = json.load(f)
            existing_data = {entry["mobile"]: entry for entry in existing_list}
            logger.info("Loaded %d existing entries from call_list.json", len(existing_data))
        except (json.JSONDecodeError, KeyError):
            logger.warning("Existing call_list.json is corrupted. Rebuilding.")
            existing_data = {}

    # Build final list, preserving existing statuses
    call_list = []
    for mobile in mobile_numbers:
        if mobile in existing_data:
            call_list.append(existing_data[mobile])
        else:
            call_list.append({
                "mobile": mobile,
                "status": "uncalled",
                "called_at": None,
            })

    with open(JSON_PATH, "w") as f:
        json.dump(call_list, f, indent=2)

    uncalled = sum(1 for e in call_list if e["status"] == "uncalled")
    called = sum(1 for e in call_list if e["status"] == "called")
    failed = sum(1 for e in call_list if e["status"] == "failed")

    logger.info(
        "Saved %d entries to %s (uncalled=%d, called=%d, failed=%d)",
        len(call_list), JSON_PATH, uncalled, called, failed,
    )


if __name__ == "__main__":
    convert()

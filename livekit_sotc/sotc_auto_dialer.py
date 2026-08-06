"""
SOTC VoiceBot Auto-Dialer
=========================
Reads call_list.json and triggers outbound calls via the C-Zentrix addlead API.
Maintains a maximum of 5 concurrent active calls by monitoring LiveKit rooms.

The dialer checks the actual number of active LiveKit rooms before each call.
It only triggers a new call when the room count is below the concurrency limit,
ensuring that the VoiceBot WebSocket is never overwhelmed.

Usage:
    python3 sotc_auto_dialer.py                 # Process all uncalled numbers
    python3 sotc_auto_dialer.py --test 8328257515  # Test with a single number
    python3 sotc_auto_dialer.py --max-concurrent 5 # Override max concurrent calls
    python3 sotc_auto_dialer.py --dry-run           # Simulate without actual API calls
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(SCRIPT_DIR, "call_list.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "auto_dialer.log")

API_BASE_URL = "https://tc.c-zentrixcloud.com/apps/addlead.php"
CAMP_NAME = "SOTC_VoiceBot"

DEFAULT_MAX_CONCURRENT = 5

# The app_id prefix used in LiveKit room names for outbound dialer calls.
# Room name format: {DIALER_APP_ID}_{mobile_number}_...
# Only rooms containing this prefix count toward the concurrency limit.
DIALER_APP_ID = "162056308"

# Time to wait between checking for call slot availability (seconds)
POLL_INTERVAL = 3

# Delay after initiating a call before checking rooms again (seconds)
# Gives time for the call to land on the WebSocket
POST_CALL_SETTLE_DELAY = 5

# LiveKit connection details (read from .env or environment)
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)
logger = logging.getLogger("sotc_auto_dialer")

# ---------------------------------------------------------------------------
# .env loader (only if env vars not already set)
# ---------------------------------------------------------------------------


def _load_env_file():
    """Load LiveKit credentials from .env file if not already in environment."""
    global LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

    if LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET:
        return  # Already set via environment

    env_path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key == "LIVEKIT_URL" and not LIVEKIT_URL:
                    LIVEKIT_URL = value
                elif key == "LIVEKIT_API_KEY" and not LIVEKIT_API_KEY:
                    LIVEKIT_API_KEY = value
                elif key == "LIVEKIT_API_SECRET" and not LIVEKIT_API_SECRET:
                    LIVEKIT_API_SECRET = value


# ---------------------------------------------------------------------------
# JSON Persistence
# ---------------------------------------------------------------------------


def load_call_list():
    """Load call_list.json."""
    if not os.path.exists(JSON_PATH):
        logger.error("call_list.json not found at %s. Run convert_xlsx_to_json.py first.", JSON_PATH)
        sys.exit(1)

    with open(JSON_PATH, "r") as f:
        return json.load(f)


def save_call_list(call_list):
    """Atomically save call_list.json using write-then-rename."""
    tmp_path = JSON_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(call_list, f, indent=2)
    os.replace(tmp_path, JSON_PATH)


def update_entry_status(call_list, mobile, status):
    """Update the status of a specific mobile entry and persist."""
    for entry in call_list:
        if entry["mobile"] == mobile:
            entry["status"] = status
            entry["called_at"] = datetime.now(timezone.utc).isoformat()
            break
    save_call_list(call_list)

# ---------------------------------------------------------------------------
# LiveKit Room Monitor
# ---------------------------------------------------------------------------


async def get_active_room_count():
    """
    Query LiveKit Server API to get the count of currently active dialer rooms.
    Only rooms whose name contains the DIALER_APP_ID are counted,
    since those are the outbound calls initiated by this dialer.
    Returns (dialer_room_count, dialer_room_names, total_room_count).
    """
    from livekit import api

    # Convert ws:// URL to http:// for API calls
    api_url = LIVEKIT_URL
    if api_url.startswith("ws://"):
        api_url = api_url.replace("ws://", "http://", 1)
    elif api_url.startswith("wss://"):
        api_url = api_url.replace("wss://", "https://", 1)

    lk = api.LiveKitAPI(
        url=api_url,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    try:
        response = await lk.room.list_rooms(api.ListRoomsRequest())
        all_rooms = response.rooms
        # Filter: only count rooms that belong to this dialer (contain the app_id)
        dialer_rooms = [r for r in all_rooms if DIALER_APP_ID in r.name]
        dialer_names = [r.name for r in dialer_rooms]
        return len(dialer_rooms), dialer_names, len(all_rooms)
    finally:
        await lk.aclose()


async def wait_for_slot(max_concurrent, dry_run=False):
    """
    Block until there is a free call slot (dialer rooms < max_concurrent).
    Returns the current dialer room count once a slot is available.
    """
    if dry_run:
        return 0

    while True:
        try:
            dialer_count, dialer_names, total_count = await get_active_room_count()
            if dialer_count < max_concurrent:
                logger.info(
                    "Slot available: %d/%d dialer rooms (total rooms: %d)",
                    dialer_count, max_concurrent, total_count,
                )
                return dialer_count
            else:
                logger.info(
                    "At capacity: %d/%d dialer rooms (total rooms: %d). Waiting %ds...",
                    dialer_count, max_concurrent, total_count, POLL_INTERVAL,
                )
                # Log which dialer rooms are active for debugging
                for name in dialer_names:
                    logger.debug("  Dialer room: %s", name)
        except Exception as e:
            logger.warning(
                "Failed to query LiveKit rooms: %s. Retrying in %ds...",
                str(e), POLL_INTERVAL,
            )

        await asyncio.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# API Call Trigger
# ---------------------------------------------------------------------------


async def trigger_call(mobile, dry_run=False):
    """
    Trigger a call by hitting the C-Zentrix addlead API.
    Returns (success: bool, response_text: str).
    """
    import urllib.request
    import urllib.error
    import urllib.parse

    # Build URL with proper encoding
    params = urllib.parse.urlencode({
        "camp_name": CAMP_NAME,
        "mobile": mobile,
    })
    url = f"{API_BASE_URL}?{params}"

    if dry_run:
        logger.info("[DRY-RUN] Would trigger call to %s", _mask_mobile(mobile))
        return True, "dry-run"

    logger.info("Triggering call to %s via API...", _mask_mobile(mobile))

    try:
        # Use asyncio to run the blocking request in a thread pool
        loop = asyncio.get_event_loop()
        response_text = await loop.run_in_executor(None, _make_request, url)
        logger.info("API response for %s: %s", _mask_mobile(mobile), response_text)
        return True, response_text
    except Exception as e:
        logger.error("API call failed for %s: %s", _mask_mobile(mobile), str(e))
        return False, str(e)


def _make_request(url):
    """Make an HTTP GET request and return the response body."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "SOTC-AutoDialer/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _mask_mobile(mobile):
    """Mask mobile number for logging (show last 4 digits only)."""
    if len(mobile) >= 4:
        return "******" + mobile[-4:]
    return "****"

# ---------------------------------------------------------------------------
# Call Manager (LiveKit-aware Concurrency Control)
# ---------------------------------------------------------------------------


class CallManager:
    """
    Manages concurrent outbound calls by monitoring LiveKit room count.
    Ensures at most `max_concurrent` calls are active on the WebSocket at
    any time by checking the actual room count before each new call.
    """

    def __init__(self, call_list, max_concurrent, dry_run=False):
        self.call_list = call_list
        self.max_concurrent = max_concurrent
        self.dry_run = dry_run
        self.total_triggered = 0
        self.total_failed = 0
        self.total_success = 0

    async def process_call(self, mobile):
        """
        Wait for a slot, then trigger a single call.
        After triggering, wait for the call to settle on the WebSocket.
        """
        # Wait until there is room in the LiveKit server
        active_count = await wait_for_slot(self.max_concurrent, self.dry_run)

        self.total_triggered += 1
        logger.info(
            "Active rooms: %d/%d | Triggering call #%d: %s",
            active_count, self.max_concurrent,
            self.total_triggered, _mask_mobile(mobile),
        )

        try:
            success, response = await trigger_call(mobile, self.dry_run)

            if success:
                update_entry_status(self.call_list, mobile, "called")
                self.total_success += 1
            else:
                update_entry_status(self.call_list, mobile, "failed")
                self.total_failed += 1

        except Exception as e:
            logger.error("Unexpected error for %s: %s", _mask_mobile(mobile), str(e))
            update_entry_status(self.call_list, mobile, "failed")
            self.total_failed += 1

        # Wait for the call to actually land on the WebSocket before
        # processing the next number. This prevents the dialer from
        # blasting through all numbers before calls connect.
        if not self.dry_run:
            logger.info(
                "Waiting %ds for call to settle on WebSocket...",
                POST_CALL_SETTLE_DELAY,
            )
            await asyncio.sleep(POST_CALL_SETTLE_DELAY)

    async def run(self, mobiles):
        """Process all mobile numbers sequentially with room-aware pacing."""
        logger.info(
            "Starting auto-dialer: %d numbers to process, max %d concurrent calls",
            len(mobiles), self.max_concurrent,
        )

        # Process calls one at a time — each call waits for a slot
        # before triggering, ensuring we never exceed the limit
        for i, mobile in enumerate(mobiles):
            logger.info("--- Queue position: %d/%d ---", i + 1, len(mobiles))
            await self.process_call(mobile)

        logger.info("=" * 60)
        logger.info("Auto-dialer completed!")
        logger.info("  Total triggered: %d", self.total_triggered)
        logger.info("  Successful:      %d", self.total_success)
        logger.info("  Failed:          %d", self.total_failed)
        logger.info("=" * 60)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_test(test_number, dry_run=False):
    """Run a test call to a single number."""
    logger.info("=" * 60)
    logger.info("TEST MODE: Calling %s", test_number)
    logger.info("=" * 60)

    success, response = await trigger_call(test_number, dry_run)
    if success:
        logger.info("Test call triggered successfully. Response: %s", response)
    else:
        logger.error("Test call failed. Response: %s", response)

    return success


async def run_dialer(max_concurrent, dry_run=False):
    """Run the full auto-dialer on all uncalled numbers."""

    # Verify LiveKit connectivity before starting
    if not dry_run:
        logger.info("Verifying LiveKit connectivity...")
        try:
            dialer_count, _, total_count = await get_active_room_count()
            logger.info(
                "LiveKit connected! %d dialer rooms, %d total rooms.",
                dialer_count, total_count,
            )
        except Exception as e:
            logger.error(
                "Cannot connect to LiveKit at %s: %s",
                LIVEKIT_URL, str(e),
            )
            logger.error("Fix LiveKit connection before running the dialer.")
            sys.exit(1)

    call_list = load_call_list()

    uncalled = [e["mobile"] for e in call_list if e["status"] == "uncalled"]
    called = sum(1 for e in call_list if e["status"] == "called")
    failed = sum(1 for e in call_list if e["status"] == "failed")

    logger.info("=" * 60)
    logger.info("SOTC VoiceBot Auto-Dialer (LiveKit-aware)")
    logger.info("  Total entries: %d", len(call_list))
    logger.info("  Already called: %d", called)
    logger.info("  Failed:         %d", failed)
    logger.info("  Uncalled:       %d", len(uncalled))
    logger.info("  Max concurrent: %d", max_concurrent)
    logger.info("  Dry run:        %s", dry_run)
    logger.info("  LiveKit URL:    %s", LIVEKIT_URL)
    logger.info("=" * 60)

    if not uncalled:
        logger.info("No uncalled numbers remaining. Exiting.")
        return

    manager = CallManager(call_list, max_concurrent, dry_run)
    await manager.run(uncalled)


def main():
    # Load .env for LiveKit credentials
    _load_env_file()

    parser = argparse.ArgumentParser(description="SOTC VoiceBot Auto-Dialer")
    parser.add_argument(
        "--test",
        type=str,
        help="Test with a single phone number (e.g., --test 8328257515)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT,
        help=f"Maximum concurrent active calls (default: {DEFAULT_MAX_CONCURRENT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate calls without actually triggering the API",
    )
    parser.add_argument(
        "--convert",
        action="store_true",
        help="Convert Book1.xlsx to call_list.json before dialing",
    )
    args = parser.parse_args()

    # Validate LiveKit config (not needed for dry-run or test mode)
    if not args.dry_run and not args.test:
        if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
            logger.error(
                "LiveKit credentials not configured. "
                "Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET "
                "in environment or .env file."
            )
            sys.exit(1)

    # Optionally convert xlsx first
    if args.convert:
        from convert_xlsx_to_json import convert
        convert()

    if args.test:
        # Test mode: call a single number
        asyncio.run(run_test(args.test, dry_run=args.dry_run))
    else:
        # Full dialer mode
        asyncio.run(run_dialer(args.max_concurrent, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

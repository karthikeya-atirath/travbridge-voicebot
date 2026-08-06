import logging
import traceback
import json
import httpx
from fastapi import HTTPException, status
from typing import Any, Optional, Dict, List
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
)
from collections import Counter
import re
from dateutil import parser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from livekit.agents import function_tool, RunContext
import os
from app_logger import applog
import chat_history as _ch_module  # lazy reference to avoid circular import
import asyncio

elastic_search_url_live_packages = "https://travbridge.atirath.com/v1/livepackages"
bogo_packages_url = "https://travbridge.atirath.com/v1/get_all_BOGO_packages"
fare_calendar_url = "https://travbridge.atirath.com/v1/search_by_package_id"
search_packages_by_name_url = "https://travbridge.atirath.com/v1/search_by_package_name_v2"
# package_pricing_url = "http:///travbridge.atirath.com/mcp/tcil/api/get_package_pricing"
package_pricing_url = "http://10.160.0.3:9005/mcp/tcil/api/get_package_pricing"
PACKAGES_NOT_FOUND = "No matching packages found for your destination, Would you like to change destination?"
ERROR_MESSAGE_FOR_MONTH = "Unfortunately, there are no active travel packages available for the requested month. "
ERROR_MESSAGE_FOR_BASE_CITY = "Unfortunately, "
CUSTOM_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# ---------- helpers ----------

_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

def _sanitize(text: str) -> str:
    """Remove NBSP & control chars; trim whitespace."""
    text = (text or "").replace("\xa0", " ")
    return _CTRL_CHARS_RE.sub("", text).strip()

def _to_int_safe(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val not in (None, "", False) else default
    except (ValueError, TypeError):
        return default

def _extract_pricing_highlights(data: Any) -> Dict[str, Any]:
    """
    Best-effort extraction of common pricing fields from a flexible upstream response.
    Also looks inside a nested 'data' dict if top-level fields are missing.
    """
    if not isinstance(data, dict):
        return {}

    highlights: Dict[str, Any] = {}
    candidate_fields = [
        "price",
        "total_price",
        "totalPrice",
        "totalTax",
        "grossPrice",
        "netPrice",
        "totalDiscount",
        "per_person_price",
        "perPersonPrice",
        "price_per_person",
        "starting_price",
        "startingPrice",
        "currency",
        "currency_code",
        "room_type",
        "roomType",
        "package_name",
        "packageName",
        "quotationId",
        "holidayQuotationId",
    ]

    # Search top-level first, then fall back to nested 'data' dict
    search_dicts = [data]
    if isinstance(data.get("data"), dict):
        search_dicts.append(data["data"])

    for source in search_dicts:
        for field in candidate_fields:
            if field not in highlights:
                value = source.get(field)
                if value not in (None, "", [], {}):
                    highlights[field] = value

    return highlights

# Module-level storage for card_data (accessed by agent_text_v2._publish_tool_event)
_latest_card_data = None


def _build_card_data(packages: list, limit: int = 6) -> list:
    """
    Build lightweight card data for the frontend data channel.
    Only includes key display fields to stay under payload size limits.
    """
    cards = []
    for pkg in (packages or [])[:limit]:
        try:
            itd = pkg.get("itinerary_data") or {}
            pkg_itinerary = itd.get("packageItinerary") or {}
            daywise = pkg_itinerary.get("itinerary") or []

            # Extract cities from itinerary days
            cities = []
            for day in daywise:
                city = _sanitize(day.get("cityName") or day.get("city") or "")
                if city and city not in cities:
                    cities.append(city)

            # Extract hotel tier details
            tour_type_details = []
            for td in (itd.get("packageTourType_details") or []):
                cat = _sanitize(td.get("category") or "")
                tier_price = td.get("price")
                if cat:
                    tour_type_details.append({"category": cat, "price": tier_price})

            # Safe nights calculation
            nights = itd.get("nights")
            if nights is None:
                try:
                    nights = int(itd.get("days") or 1) - 1
                except (ValueError, TypeError):
                    nights = 0

            cards.append({
                "packageId": itd.get("packageId") or "",
                "packageName": _sanitize(itd.get("packageName") or "Unknown"),
                "pkgSubtypeName": _sanitize(itd.get("pkgSubtypeName") or ""),
                "days": itd.get("days"),
                "nights": nights,
                "price": itd.get("price"),
                "packageTourType": itd.get("packageTourType") or [],
                "packageTourType_details": tour_type_details,
                "thumbnailImage": itd.get("constructed_thumbnailImage") or "",
                "cities": cities,
                "flightsAvailability": _sanitize(itd.get("flightsAvailability") or ""),
            })
        except Exception as e:
            applog.error(f"[CARD_DATA] Error building card for package: {e}", exc_info=True)
            continue
    applog.info(f"[CARD_DATA] Built {len(cards)} card(s) from {min(len(packages or []), limit)} packages")
    return cards


def build_package_summaries(result: Any) -> List[str]:
    """
    Build short, sanitized summaries (kept small for realtime APIs).
    """
    applog.info("build_package_summaries called")
    if not result:
        applog.info("No travel packages found.")
        return []

    package_list: List[str] = []
    try:
        for package in (result or []):
            itinerary_data = package.get("itinerary_data") or {}
            name = _sanitize(itinerary_data.get("packageName") or "Unknown")
            price_val = itinerary_data.get("price")
            price = str(price_val) if price_val is not None else "Not available"
            days_val = itinerary_data.get("days")
            days = str(days_val) if days_val is not None else "Not available"
            pkgSubtypeId = itinerary_data["pkgSubtypeId"]
            # packageTourType is a list of strings e.g. ['Standard', 'Value', 'Premium']
            # packageTourType_details is a list of dicts: [{category, price, hotels}, ...]
            # pkgType mapping for get_package_pricing: Standard=0, Value=1, Premium=2
            _type_to_int = {"standard": 0, "value": 1, "premium": 2}
            package_tour_types = itinerary_data.get("packageTourType") or []
            if isinstance(package_tour_types, str):
                package_tour_types = [package_tour_types]  # safety fallback
            package_tour_type_details = itinerary_data.get("packageTourType_details") or []
            package_itinerary = itinerary_data.get("packageItinerary") or {}
            daywise = package_itinerary.get("itinerary") or []

            # Get inclusions, exclusions, and flight availability
            inclusions = _sanitize(itinerary_data.get("inclusions") or "")
            exclusions = _sanitize(itinerary_data.get("exclusions") or "")
            flights_availability = _sanitize(itinerary_data.get("flightsAvailability") or "")

            pieces = [f"Package: {name}. Price: {price} rupees. Duration: {days} days."]
            pieces.append(f"pkgSubtypeId: {pkgSubtypeId}")

            # Available hotel tiers for this package
            if package_tour_types:
                tiers_str = ", ".join(package_tour_types)
                pieces.append(f"Available hotel categories: {tiers_str}")

            # Per-tier price breakdown (Standard=0, Value=1, Premium=2 for get_package_pricing)
            if package_tour_type_details:
                tier_pieces = []
                for td in package_tour_type_details:
                    cat = _sanitize(td.get("category") or "")
                    tier_price = td.get("price")
                    pkg_type_int = _type_to_int.get(cat.lower(), "?")
                    hotels_in_tier = td.get("hotels") or []
                    if isinstance(hotels_in_tier, list):
                        hotels_in_tier_text = ", ".join(_sanitize(str(h)) for h in hotels_in_tier if h)
                    else:
                        hotels_in_tier_text = _sanitize(str(hotels_in_tier))
                    if cat:
                        tier_entry = f"{cat} (pkgType={pkg_type_int}): {tier_price} rupees"
                        if hotels_in_tier_text:
                            tier_entry += f" | Hotels: {hotels_in_tier_text}"
                        tier_pieces.append(tier_entry)
                if tier_pieces:
                    pieces.append(f"Hotel tier details: {'; '.join(tier_pieces)}")
            packageId=itinerary_data["packageId"]
            pieces.append(f"packageId: {packageId}")
            # Add flight availability if available
            if flights_availability:
                pieces.append(f"Flight Availability: {flights_availability}")

            # Add inclusions if available
            if inclusions:
                pieces.append(f"Inclusions: {inclusions}")

            # Add exclusions if available
            if exclusions:
                pieces.append(f"Exclusions: {exclusions}")

            # --- Additional package details ---

            # Tour Manager Description
            tour_mgr_desc = _sanitize(itinerary_data.get("tourManagerDescription") or "")
            if tour_mgr_desc:
                pieces.append(f"Tour Manager: {tour_mgr_desc}")

            # Meals
            meals = itinerary_data.get("meals") or []
            if meals and isinstance(meals, list):
                meals_text = ", ".join(_sanitize(str(m)) for m in meals if m)
                if meals_text:
                    pieces.append(f"Meals: {meals_text}")

            # Flight Description
            flight_desc = _sanitize(itinerary_data.get("flightDescription") or "")
            if flight_desc:
                pieces.append(f"Flight Details: {flight_desc}")

            # Sightseeing
            sightseeing = itinerary_data.get("sightseeing") or []
            if sightseeing and isinstance(sightseeing, list):
                sight_text = ", ".join(_sanitize(str(s)) for s in sightseeing if s)
                if sight_text:
                    pieces.append(f"Sightseeing: {sight_text}")

            # Hotels
            hotels = itinerary_data.get("hotels") or []
            if hotels and isinstance(hotels, list):
                hotel_items = []
                for h in hotels:
                    if isinstance(h, dict):
                        h_name = _sanitize(h.get("hotelName") or h.get("name") or "")
                        h_rating = h.get("starRating") or h.get("rating") or ""
                        h_city = _sanitize(h.get("city") or h.get("location") or "")
                        parts = [p for p in [h_name, f"{h_rating}-star" if h_rating else "", h_city] if p]
                        if parts:
                            hotel_items.append(" ".join(parts))
                    elif h:
                        hotel_items.append(_sanitize(str(h)))
                if hotel_items:
                    pieces.append(f"Hotels: {'; '.join(hotel_items)}")

            # Highlights
            highlights = itinerary_data.get("highlights") or []
            if highlights and isinstance(highlights, list):
                hl_text = "; ".join(_sanitize(str(h)) for h in highlights if h)
                if hl_text:
                    pieces.append(f"Highlights: {hl_text}")

            # Include ALL days so bot can answer any day-specific questions
            for day in daywise:  # Include all days of itinerary
                dnum = str(day.get("day") or "")
                desc = _sanitize(day.get("description") or "No description available.")
                pieces.append(f"Day {dnum}: {desc}")

            summary = " ".join(pieces)
            # No truncation — LLM needs complete package info to answer all questions
            package_list.append(summary)

        # cap total number of summaries to keep context manageable
        return package_list[:6]
    except Exception:
        applog.error("exception building package list:\n" + traceback.format_exc())
        return []

# ---------- tool ----------

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
async def get_travel_package(
    context: RunContext,  # type: ignore
    destination: str,                 # <-- make optional
    number_of_people: int = 0,
    days: int = 0,
    budget: int = 0,
    hub: Optional[str] = None,
    month_of_travel: Optional[str] = None,
    package_type: str = "GIT,FIT",       # <-- GIT, FIT, or both (default: both)
) -> Dict[str, Any]:                     # <-- always return a dict
    """
    Retrieve live travel packages. 
    CRITICAL RULE: Do NOT call this tool until you have asked the user for and collected ALL 7 pieces of information: Destination, Departure city (hub), Travel dates (month_of_travel), Number of days (days), Budget, Number of people (number_of_people), and Package preference (package_type).
    If any of these are missing, DO NOT CALL THIS TOOL. Instead, ask the user for the missing details first.
    Package type can be "GIT" (Group Inclusive Tour), "FIT" (Free Independent Tour), or "GIT,FIT" for both (default).
    """
    # ── Log function call to chat history ─────────────────────────────────
    _args_str = json.dumps({
        "destination": destination,
        "number_of_people": number_of_people, "days": days,
        "budget": budget, "hub": hub,
        "month_of_travel": month_of_travel, "package_type": package_type,
    })
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call("get_travel_package", _args_str)
    # ──────────────────────────────────────────────────────────────────────
    try:
        applog.info(
            f"retrieve_live_package called with input="
            f"{destination=}, {number_of_people=}, {days=}, {budget=}, {hub=}, {month_of_travel=}, {package_type=}"
        )

        if not destination:
            return {"message": "Please share a destination (e.g., Paris, Europe, Bali).", "code": 400}

        # Convert values safely
        budget = _to_int_safe(budget, 0)
        days = _to_int_safe(days, 0)
        number_of_people = _to_int_safe(number_of_people, 0)

        # sanitize strings
        destination = _sanitize(destination)
        hub = _sanitize(hub or "")
        month_of_travel = _sanitize(month_of_travel or "")  # <-- keep user's month; don't blank it
        package_type = _sanitize(package_type or "GIT,FIT").upper()  # <-- normalize to uppercase

        # Validate package_type and convert to API format
        valid_types = []
        if "GIT" in package_type:
            valid_types.append("GIT")
        if "FIT" in package_type:
            valid_types.append("FIT")

        # If no valid type found, default to both
        if not valid_types:
            valid_types = ["GIT", "FIT"]

        pkgSubtypeName = ",".join(valid_types)

        payload = {
            "search_term": destination,
            "number_of_people": number_of_people,
            "days": days,
            "budget": budget,
            "departureCity": hub,
            "monthOfTravel": month_of_travel,
            "pkgSubtypeName": pkgSubtypeName,
            "fareCalendar": False,
        }

        headers = {"Content-Type": "application/json"}
        applog.info(f"Payload: {payload}")

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
            response = await client.post(
                elastic_search_url_live_packages, json=payload, headers=headers
            )

            # Protect against non-JSON or huge error responses

            try:
                response_data = response.json()
                applog.info(f"Response Data: {response_data}")
            except Exception:
                applog.error(f"Non-JSON response: {response.text[:500]}")
                return {"message": "Upstream service error. Please try again.", "code": 502}

            message = response_data.get("message", PACKAGES_NOT_FOUND)

            if response.status_code == 200:
                applog.info("✅ API call successful.")
                result = response_data.get("body", [])

                # Count GIT and FIT packages from API response
                git_count = 0
                fit_count = 0
                for package in (result or []):
                    itinerary_data = package.get("itinerary_data") or {}
                    pkg_subtype = itinerary_data.get("pkgSubtypeName", "").upper()
                    if "GIT" in pkg_subtype:
                        git_count += 1
                    if "FIT" in pkg_subtype:
                        fit_count += 1

                # Log package counts from API
                total_packages = len(result or [])
                applog.info(f"📦 Total packages returned from API: {total_packages}")
                applog.info(f"👥 GIT packages: {git_count}")
                applog.info(f"🏖️ FIT packages: {fit_count}")

                # Filter packages based on user's requested package_type
                filtered_packages = []
                requested_types = pkgSubtypeName.split(",")  # Get the types user requested

                for package in (result or []):
                    itinerary_data = package.get("itinerary_data") or {}
                    pkg_subtype = itinerary_data.get("pkgSubtypeName", "").upper()

                    # Check if package matches any of the requested types
                    should_include = False
                    for req_type in requested_types:
                        if req_type.strip() in pkg_subtype:
                            should_include = True
                            break

                    if should_include:
                        filtered_packages.append(package)

                applog.info(f"🔍 Filtered packages based on request ({pkgSubtypeName}): {len(filtered_packages)}")

                packages_titles = build_package_summaries(filtered_packages)
                # Build card_data and store in module-level var for data channel
                # (NOT in tool return — keeps IPC payload small)
                global _latest_card_data
                _latest_card_data = _build_card_data(filtered_packages, limit=6)
                applog.info(f"Button Message List: {packages_titles}")
                _result = {
                    "packages": packages_titles,
                    "count": len(packages_titles),
                    "destination": destination,
                    "month": month_of_travel,
                }
                if ch:
                    ch.add_function_call_output("get_travel_package", json.dumps(_result))
                return _result


            if response.status_code == 401:
                # month-specific error
                return {
                    "message": f"Note: {ERROR_MESSAGE_FOR_MONTH}\n{message}",
                    "code": 401,
                }

            if response.status_code == 402:
                # base city-specific error
                return {
                    "message": f"Note: {ERROR_MESSAGE_FOR_BASE_CITY}{message}",
                    "code": 402,
                }

            # generic not found/other
            return {
                "message": f"Note: {PACKAGES_NOT_FOUND}\n{message}",
                "code": status.HTTP_404_NOT_FOUND,
            }

    except HTTPException as e:
        applog.error(f"HTTPException: {e}")
        _err_result = {"message": "Request failed. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("get_travel_package", json.dumps(_err_result))
        return _err_result
    except Exception as err:
        applog.error(f"Unexpected error: {err}\n{traceback.format_exc()}")
        _err_result = {"message": "Unexpected error. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("get_travel_package", json.dumps(_err_result))
        return _err_result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
async def get_all_bogo_packages(
    context: RunContext,  # type: ignore
) -> Dict[str, Any]:
    """
    Retrieve all BOGO (Buy One Get One) packages. Returns package names, duration, and prices.
    """
    # ── Log function call to chat history ─────────────────────────────────
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call("get_all_bogo_packages", "{}")
    # ──────────────────────────────────────────────────────────────────────
    try:
        applog.info("get_all_bogo_packages called")

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
            response = await client.get(bogo_packages_url)

            try:
                response_data = response.json()
                applog.info(f"BOGO API Response: {response_data}")
            except Exception:
                applog.error(f"Non-JSON response from BOGO API: {response.text[:500]}")
                return {"message": "Failed to fetch BOGO packages. Please try again.", "code": 502}

            if response.status_code == 200:
                applog.info("✅ BOGO API call successful.")
                packages_data = response_data if isinstance(response_data, list) else []

                if not packages_data:
                    return {
                        "message": "No BOGO packages available at the moment.",
                        "code": 404,
                    }

                # Build formatted string from package data
                bogo_packages_list = []
                for package in packages_data:
                    itinerary_data = package.get("itinerary_data", {})

                    package_name = _sanitize(itinerary_data.get("packageName", "Unknown Package"))
                    days = itinerary_data.get("days", "N/A")
                    price = itinerary_data.get("price", "N/A")

                    # Create formatted string for each package
                    package_info = f"Package: {package_name}, Duration: {days} days, Price: {price} rupees"
                    bogo_packages_list.append(package_info)

                # Join all packages into a single string
                bogo_packages_string = " | ".join(bogo_packages_list)

                applog.info(f"BOGO Packages String: {bogo_packages_string}")

                # Store card_data for frontend data channel
                global _latest_card_data
                _latest_card_data = _build_card_data(packages_data, limit=6)

                _bogo_result = {
                    "packages": bogo_packages_string,
                    "count": len(bogo_packages_list),
                    "message": "Successfully retrieved BOGO packages.",
                }
                if ch:
                    ch.add_function_call_output("get_all_bogo_packages", json.dumps(_bogo_result))
                return _bogo_result
            else:
                applog.error(f"BOGO API returned status code: {response.status_code}")
                return {
                    "message": "Failed to fetch BOGO packages. Please try again.",
                    "code": response.status_code,
                }

    except HTTPException as e:
        applog.error(f"HTTPException in get_all_bogo_packages: {e}")
        return {"message": "Request failed. Please try again.", "code": 500}
    except Exception as err:
        applog.error(f"Unexpected error in get_all_bogo_packages: {err}\n{traceback.format_exc()}")
        return {"message": "Unexpected error. Please try again.", "code": 500}


# ---------- fare calendar helpers ----------

def _fmt_date_voice(date_str: str) -> str:
    """Convert 'DD-MM-YYYY' to a voice-friendly string like 'March 19, 2026'."""
    try:
        return datetime.strptime(date_str, "%d-%m-%Y").strftime("%B %-d, %Y")
    except Exception:
        return date_str


def _build_fit_calendar_summary(fare_calendar: dict) -> tuple[str, dict]:
    """
    Build a voice-friendly summary for FIT fare calendar.
    Returns: (summary_text, all_dates_dict) where all_dates_dict maps class names to list of all bookable dates
    """
    class_types = fare_calendar.get("classTypes") or []
    if not class_types:
        return "No availability information found for this package.", {}

    lines = []
    all_dates_by_class = {}

    for ct in class_types:
        class_name = _sanitize(ct.get("className") or "")
        avail = ct.get("availability") or {}
        stats = avail.get("stats") or {}
        date_range = avail.get("dateRange") or {}
        is_available = avail.get("isAvailable", False)

        if not is_available:
            lines.append(f"{class_name} class: currently not available.")
            continue

        start = _fmt_date_voice(date_range.get("startDate", ""))
        end = _fmt_date_voice(date_range.get("endDate", ""))
        min_price = stats.get("minPrice")
        max_price = stats.get("maxPrice")
        total_bookable = stats.get("totalBookableDates", 0)
        total_on_request = stats.get("totalOnRequestDates", 0)

        price_text = ""
        if min_price and max_price:
            if min_price == max_price:
                price_text = f"Price: ₹{min_price:,} per person."
            else:
                price_text = f"Price: ₹{min_price:,} to ₹{max_price:,} per person."

        range_text = f"Available from {start} to {end}." if start and end else ""
        count_text = f"{total_bookable} bookable dates"
        if total_on_request:
            count_text += f" and {total_on_request} on-request dates"
        count_text += "."

        # Get ALL bookable dates for reference
        bookable_dates = (ct.get("dates") or {}).get("bookable") or []
        all_dates_by_class[class_name] = [d["date"] for d in bookable_dates if d.get("date")]

        # Show first 5 for voice summary
        upcoming = [_fmt_date_voice(d["date"]) for d in bookable_dates[:5] if d.get("date")]
        upcoming_text = ""
        if upcoming:
            upcoming_text = f"Sample upcoming dates: {', '.join(upcoming)}."

        parts = [p for p in [class_name + " class:", range_text, price_text, count_text, upcoming_text] if p]
        lines.append(" ".join(parts))

    return " | ".join(lines), all_dates_by_class


def _build_git_calendar_summary(fare_calendar: dict) -> tuple[str, dict]:
    """
    Build a voice-friendly summary for GIT fare calendar.
    GIT packages use departureCities structure (not classTypes like FIT).
    Returns: (summary_text, all_dates_dict) where all_dates_dict maps departure city to list of all bookable dates
    """
    # GIT structure uses departureCities array
    departure_cities = fare_calendar.get("departureCities") or []

    if not departure_cities:
        # Fallback: try old classTypes structure or flat dates list
        class_types = fare_calendar.get("classTypes") or []
        if class_types:
            # Use old logic for classTypes
            lines = []
            all_dates_by_class = {}
            for ct in class_types:
                class_name = _sanitize(ct.get("className") or "Group")
                avail = ct.get("availability") or {}
                stats = avail.get("stats") or {}
                date_range = avail.get("dateRange") or {}
                is_available = avail.get("isAvailable", False)
                if not is_available:
                    continue
                start = _fmt_date_voice(date_range.get("startDate", ""))
                end = _fmt_date_voice(date_range.get("endDate", ""))
                min_price = stats.get("minPrice")
                max_price = stats.get("maxPrice")
                price_text = ""
                if min_price and max_price:
                    if min_price == max_price:
                        price_text = f"Price: ₹{min_price:,} per person."
                    else:
                        price_text = f"Price from ₹{min_price:,} to ₹{max_price:,} per person."
                range_text = f"Departures available from {start} to {end}." if start and end else ""
                bookable_dates = (ct.get("dates") or {}).get("bookable") or []
                all_dates_by_class[class_name] = [d["date"] for d in bookable_dates if d.get("date")]
                upcoming = [_fmt_date_voice(d["date"]) for d in bookable_dates[:5] if d.get("date")]
                upcoming_text = f"Sample departure dates: {', '.join(upcoming)}." if upcoming else ""
                parts = [p for p in [class_name + ":", range_text, price_text, upcoming_text] if p]
                lines.append(" ".join(parts))
            return " | ".join(lines), all_dates_by_class

        # Final fallback: try flat dates
        dates = fare_calendar.get("dates") or []
        if not dates:
            return "No availability information found for this group tour package.", {}
        all_dates = [d["date"] for d in dates if d.get("date")]
        upcoming = [_fmt_date_voice(d["date"]) for d in dates[:5] if d.get("date")]
        return f"Sample upcoming group tour departure dates: {', '.join(upcoming)}.", {"Group": all_dates}

    # Process departureCities structure (modern GIT format)
    lines = []
    all_dates_by_city = {}

    for city_data in departure_cities:
        city_name = _sanitize(city_data.get("cityName") or "")
        avail = city_data.get("availability") or {}
        stats = avail.get("stats") or {}
        date_range = avail.get("dateRange") or {}
        is_available = avail.get("isAvailable", False)

        if not is_available:
            continue

        start = _fmt_date_voice(date_range.get("startDate", ""))
        end = _fmt_date_voice(date_range.get("endDate", ""))
        min_price = stats.get("minPrice")
        max_price = stats.get("maxPrice")
        total_bookable = stats.get("totalBookableDates", 0)
        total_on_request = stats.get("totalOnRequestDates", 0)

        price_text = ""
        if min_price and max_price:
            if min_price == max_price:
                price_text = f"Price: ₹{min_price:,} per person."
            else:
                price_text = f"Price from ₹{min_price:,} to ₹{max_price:,} per person."

        range_text = f"Departures available from {start} to {end}." if start and end else ""
        count_text = f"{total_bookable} departure dates available"
        if total_on_request:
            count_text += f" and {total_on_request} on-request"
        count_text += "."

        # Get ALL bookable dates for reference
        dates_obj = city_data.get("dates") or {}
        bookable_dates = dates_obj.get("bookable") or []
        all_dates_by_city[city_name] = [d["date"] for d in bookable_dates if d.get("date")]

        # Show first 5 for voice summary
        upcoming = [_fmt_date_voice(d["date"]) for d in bookable_dates[:5] if d.get("date")]
        upcoming_text = f"Sample departure dates: {', '.join(upcoming)}." if upcoming else ""

        parts = [p for p in [range_text, price_text, count_text, upcoming_text] if p]
        lines.append(" ".join(parts))

    return " | ".join(lines), all_dates_by_city


# ---------- fare calendar tool ----------

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
async def get_fare_calendar(
    context: RunContext,  # type: ignore
    package_id: str,
    departure_city: str,
) -> Dict[str, Any]:
    """
    Fetch available travel dates and prices for a specific package using its packageId.
    Call this tool after the customer expresses interest in a specific package to show
    them when they can travel and at what price.
    """
    # ── Log function call to chat history ─────────────────────────────────
    _fc_args = json.dumps({"package_id": package_id, "departure_city": departure_city})
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call("get_fare_calendar", _fc_args)
    # ──────────────────────────────────────────────────────────────────────
    try:
        applog.info(f"get_fare_calendar called: {package_id=}, {departure_city=}")

        if not package_id:
            return {"message": "Package ID is required to fetch available dates.", "code": 400}
        if not departure_city:
            return {"message": "Departure city is required to fetch available dates.", "code": 400}

        payload = {
            "packageId": package_id.strip(),
            "departureCity": departure_city.strip(),
            "fareCalendar": True,
        }
        headers = {"Content-Type": "application/json"}
        applog.info(f"Fare calendar payload: {payload}")

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
            response = await client.post(fare_calendar_url, json=payload, headers=headers)

            try:
                response_data = response.json()
                applog.info(f"Fare calendar response: {response_data}")
            except Exception:
                applog.error(f"Non-JSON fare calendar response: {response.text[:500]}")
                return {"message": "Upstream service error. Please try again.", "code": 502}

            if response.status_code != 200:
                msg = response_data.get("message", "No availability found for this package.")
                return {"message": msg, "code": response.status_code}

            # The API returns the package data directly, not wrapped in a "body" array
            itinerary_data = response_data.get("itinerary_data") or {}
            fare_calendar = response_data.get("fareCalendar") or {}

            # ── Fallback: if fareCalendar is null, retry with "Joining Direct" ──
            # Some packages only support "Joining Direct" (customer joins at destination).
            if not fare_calendar and departure_city.strip().lower() != "joining direct":
                applog.info(f"[FARE_CAL] fareCalendar is null for '{departure_city}', retrying with 'Joining Direct'")
                jd_payload = {
                    "packageId": package_id.strip(),
                    "departureCity": "Joining Direct",
                    "fareCalendar": True,
                }
                jd_response = await client.post(fare_calendar_url, json=jd_payload, headers=headers)
                try:
                    jd_data = jd_response.json()
                except Exception:
                    jd_data = {}
                if jd_response.status_code == 200 and jd_data.get("fareCalendar"):
                    applog.info("[FARE_CAL] 'Joining Direct' fallback succeeded")
                    response_data = jd_data
                    itinerary_data = response_data.get("itinerary_data") or {}
                    fare_calendar = response_data.get("fareCalendar") or {}
                    departure_city = "Joining Direct"

            if not itinerary_data:
                return {"message": "No availability found for this package.", "code": 404}

            package_name = _sanitize(itinerary_data.get("packageName") or package_id)
            pkg_subtype = (itinerary_data.get("pkgSubtypeName") or "FIT").upper()
            days = itinerary_data.get("days", "")
            available_months = itinerary_data.get("availableMonths") or []

            # Build calendar summary based on package type
            if "GIT" in pkg_subtype:
                calendar_summary, all_dates_by_class = _build_git_calendar_summary(fare_calendar)
            else:
                calendar_summary, all_dates_by_class = _build_fit_calendar_summary(fare_calendar)

            # Format available months for voice
            months_text = ""
            if available_months:
                readable_months = [m.replace("_", " ").title() for m in available_months]
                months_text = "Available months: " + ", ".join(readable_months) + "."

            # Format all dates for LLM reference (convert to voice-friendly format)
            all_dates_formatted = {}
            for class_name, date_list in all_dates_by_class.items():
                # Convert all dates to readable format
                all_dates_formatted[class_name] = [_fmt_date_voice(d) for d in date_list]

            # Build a complete dates reference string
            dates_reference = ""
            if all_dates_formatted:
                for class_name, dates in all_dates_formatted.items():
                    dates_reference += f"{class_name} - Complete list of ALL {len(dates)} bookable dates: {', '.join(dates)}. "

            result = {
                "packageId": package_id,
                "packageName": package_name,
                "packageType": pkg_subtype,
                "days": days,
                "departureCity": departure_city,
                "availableMonths": months_text,
                "calendarSummary": calendar_summary,
                "allBookableDates": dates_reference.strip(),
            }
            applog.info(f"Fare calendar result: {result}")
            if ch:
                ch.add_function_call_output("get_fare_calendar", json.dumps(result))
            return result

    except HTTPException as e:
        applog.error(f"HTTPException in get_fare_calendar: {e}")
        return {"message": "Request failed. Please try again.", "code": 500}
    except Exception as err:
        applog.error(f"Unexpected error in get_fare_calendar: {err}\n{traceback.format_exc()}")
        return {"message": "Unexpected error. Please try again.", "code": 500}


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
async def get_package_pricing(
    context: RunContext,  # type: ignore
    pkg_id: str,
    departure_date: str,
    hub_city: str,
    rooms: list,
    is_flight_enabled: bool = False,
    safe_room_fallback_calculation_pricing: bool = False,
    pkgType: int = 0,
) -> Dict[str, Any]:
    """
    Fetch live pricing for a package for a specific departure date and room configuration.
    Use this after the customer confirms the package and preferred travel date.

    rooms must be a list of room objects, each with:
      - roomNo (int): sequential room number starting from 1
      - noAdult (int): number of adults in this room
      - noCwb (int): number of children with bed
      - noCnbS (int): number of children without bed
      - inf (int): number of infants
      - pax (int): total people in this room (noAdult + noCwb + noCnbS + inf)

    Example rooms for 2 adults + 1 child without bed:
      [{"roomNo": 1, "noAdult": 2, "noCwb": 0, "noCnbS": 1, "inf": 0, "pax": 3}]
    """
    _args_str = json.dumps({
        "pkg_id": pkg_id,
        "departure_date": departure_date,
        "hub_city": hub_city,
        "rooms": rooms,
        "user_mobile_no": "999999999",
        "user_email_id": "test@test.com",
        "is_flight_enabled": is_flight_enabled,
        "safe_room_fallback_calculation_pricing": safe_room_fallback_calculation_pricing,
        "pkg_class_id": pkgType,
    })
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call("get_package_pricing", _args_str)

    try:
        applog.info(
            "get_package_pricing called with "
            f"{pkg_id=}, {departure_date=}, {hub_city=}, {rooms=}, "
            f"{is_flight_enabled=}, {pkgType=}"
        )

        if not pkg_id:
            return {"message": "Package ID is required to fetch pricing.", "code": 400}
        if not departure_date:
            return {"message": "Departure date is required to fetch pricing.", "code": 400}
        if not hub_city:
            return {"message": "Departure city is required to fetch pricing.", "code": 400}
        if not rooms:
            return {"message": "Room configuration is required to fetch pricing.", "code": 400}

        # Validate and sanitize room objects
        sanitized_rooms = []
        for room in rooms:
            sanitized_rooms.append({
                "roomNo": _to_int_safe(room.get("roomNo", len(sanitized_rooms) + 1)),
                "noAdult": _to_int_safe(room.get("noAdult", 0)),
                "noCwb": _to_int_safe(room.get("noCwb", 0)),
                "noCnbS": _to_int_safe(room.get("noCnbS", 0)),
                "inf": _to_int_safe(room.get("inf", 0)),
                "pax": _to_int_safe(room.get("pax", 0)),
            })

        payload = {
            "pkg_id": _sanitize(pkg_id),
            "departure_date": _sanitize(departure_date),
            "hub_city": _sanitize(hub_city),
            "rooms": sanitized_rooms,
            "user_mobile_no": _sanitize("999999999"),
            "user_email_id": _sanitize("test@test.com"),
            "is_flight_enabled": bool(is_flight_enabled),
            "safe_room_fallback_calculation_pricing": bool(safe_room_fallback_calculation_pricing),
            "pkg_class_id": _sanitize(str(pkgType)),
        }

        headers = {"Content-Type": "application/json"}
        applog.info(f"Package pricing payload: {payload}")

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
            response = await client.post(package_pricing_url, json=payload, headers=headers)

            try:
                response_data = response.json()
                applog.info(f"Package pricing response: {response_data}")
            except Exception:
                applog.error(f"Non-JSON package pricing response: {response.text[:500]}")
                _non_json_result = {"message": "Upstream pricing service error. Please try again.", "code": 502}
                if ch:
                    ch.add_function_call_output("get_package_pricing", json.dumps(_non_json_result))
                return _non_json_result

            if response.status_code != 200:
                message = (
                    response_data.get("message")
                    if isinstance(response_data, dict)
                    else "Failed to fetch package pricing."
                )
                _error_result = {
                    "message": message or "Failed to fetch package pricing.",
                    "code": response.status_code,
                }
                if ch:
                    ch.add_function_call_output("get_package_pricing", json.dumps(_error_result))
                return _error_result

            pricing_body = response_data.get("body", response_data) if isinstance(response_data, dict) else response_data

            # ── Create a minimal pricing details object for the UI to prevent payload size limits ──
            if isinstance(pricing_body, dict):
                source = pricing_body.get("data") if isinstance(pricing_body.get("data"), dict) else pricing_body
                
                # Extract flights from flightOptions
                flight_options = source.get("flightOptions", {})
                extracted_flights = []
                if flight_options:
                    for direction in ["onward", "return"]:
                        if direction in flight_options:
                            opts = flight_options[direction].get("options", [])
                            if opts:
                                best_opt = next((o for o in opts if o.get("recommended")), opts[0])
                                extracted_flights.append({
                                    "flightNo": best_opt.get("flightNumber"),
                                    "airline": best_opt.get("airlineName"),
                                    "departureCity": best_opt.get("departure", {}).get("cityName"),
                                    "arrivalCity": best_opt.get("arrival", {}).get("cityName"),
                                    "duration": best_opt.get("duration"),
                                    "direction": direction
                                })

                mini_pricing_details = {
                    "totalPrice": source.get("totalPrice"),
                    "netPrice": source.get("netPrice"),
                    "grossPrice": source.get("grossPrice"),
                    "totalTax": source.get("totalTax"),
                    "totalDiscount": source.get("totalDiscount"),
                    "currencySummary": source.get("currencySummary"),
                    "rooms": source.get("rooms"),
                    "flights": extracted_flights if extracted_flights else source.get("flights"),
                }
                mini_pricing_details = {k: v for k, v in mini_pricing_details.items() if v is not None}
            else:
                mini_pricing_details = pricing_body

            result = {
                "message": (
                    response_data.get("message", "Pricing fetched successfully.")
                    if isinstance(response_data, dict)
                    else "Pricing fetched successfully."
                ),
                "pkg_id": payload["pkg_id"],
                "departure_date": payload["departure_date"],
                "hub_city": payload["hub_city"],
                "pricing_summary": _extract_pricing_highlights(pricing_body),
                "pricing_details": mini_pricing_details,
                "is_flight_enabled": payload["is_flight_enabled"],
                "code": 200,
            }
            if ch:
                ch.add_function_call_output("get_package_pricing", json.dumps(result))
            return result

    except HTTPException as e:
        applog.error(f"HTTPException in get_package_pricing: {e}")
        _err_result = {"message": "Request failed. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("get_package_pricing", json.dumps(_err_result))
        return _err_result
    except Exception as err:
        applog.error(f"Unexpected error in get_package_pricing: {err}\n{traceback.format_exc()}")
        _err_result = {"message": "Unexpected error. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("get_package_pricing", json.dumps(_err_result))
        return _err_result


# ---------- search packages by name tool ----------

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
async def search_packages_by_name(
    context: RunContext,  # type: ignore
    package_name: str,
) -> Dict[str, Any]:
    """
    Search for travel packages by package name. Use this when the customer mentions a specific package name
    or wants to find packages with specific names like "Singapore", "Bali", "Buy 1 Get 1 Free", etc.
    """
    # ── Log function call to chat history ─────────────────────────────────
    _args_str = json.dumps({"package_name": package_name})
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call("search_packages_by_name", _args_str)
    # ──────────────────────────────────────────────────────────────────────
    try:
        applog.info(f"search_packages_by_name called with: {package_name=}")

        if not package_name:
            return {"message": "Please provide a package name to search for.", "code": 400}

        # Sanitize package name
        package_name = _sanitize(package_name)

        payload = {
            "packageName": package_name
        }

        headers = {"Content-Type": "application/json"}
        applog.info(f"Search packages by name payload: {payload}")

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
            response = await client.post(
                search_packages_by_name_url, json=payload, headers=headers
            )

            try:
                response_data = response.json()
                applog.info(f"Search packages by name response: {response_data}")
            except Exception:
                applog.error(f"Non-JSON response: {response.text[:500]}")
                return {"message": "Upstream service error. Please try again.", "code": 502}

            if response.status_code == 200:
                applog.info("✅ Search packages by name API call successful.")
                result = []
                if isinstance(response_data, list):
                    # New API returns a raw list of {id, itinerary_data, score, ...}
                    result = response_data
                elif isinstance(response_data, dict):
                    result = response_data.get("body") or response_data.get("data") or []

                if not result:
                    return {
                        "message": f"No packages found matching '{package_name}'.",
                        "code": 404,
                    }

                # Build package summaries using the existing helper function
                packages_titles = build_package_summaries(result)
                applog.info(f"Search results: {len(packages_titles)} packages found")

                # Store card_data for frontend data channel
                global _latest_card_data
                _latest_card_data = _build_card_data(result, limit=6)

                _result = {
                    "packages": packages_titles,
                    "count": len(packages_titles),
                    "search_term": package_name,
                    "message": f"Found {len(packages_titles)} packages matching '{package_name}'.",
                }
                if ch:
                    ch.add_function_call_output("search_packages_by_name", json.dumps(_result))
                return _result
            else:
                message = response_data.get("message", "No packages found.")
                applog.error(f"API returned status code: {response.status_code}")
                return {
                    "message": message,
                    "code": response.status_code,
                }

    except HTTPException as e:
        applog.error(f"HTTPException in search_packages_by_name: {e}")
        _err_result = {"message": "Request failed. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("search_packages_by_name", json.dumps(_err_result))
        return _err_result
    except Exception as err:
        applog.error(f"Unexpected error in search_packages_by_name: {err}\n{traceback.format_exc()}")
        _err_result = {"message": "Unexpected error. Please try again.", "code": 500}
        if ch:
            ch.add_function_call_output("search_packages_by_name", json.dumps(_err_result))
        return _err_result

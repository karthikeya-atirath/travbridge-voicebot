import os
import re
import json
import asyncio
import traceback
from typing import Annotated, Any, Dict, List, Optional, Type, Tuple, Literal

import httpx

import google.auth
import google.auth.transport.requests
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_random_exponential
from livekit.agents import function_tool, RunContext

from app_logger import applog
import chat_history as _ch_module


# =============================================================================
# CONFIG
# =============================================================================


GCP_LOCATION = os.getenv("GCP_LOCATION", "asia-south1").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

# Removed GOOGLE_APPLICATION_CREDENTIALS check as we use GOOGLE_API_KEY

CUSTOM_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

_vertex_client: Optional[genai.Client] = None
_places_credentials = None
_places_auth_req = None
_places_lock = asyncio.Lock()


# =============================================================================
# AUTH / CLIENTS
# =============================================================================

def _get_vertex_client() -> genai.Client:
    global _vertex_client
    if _vertex_client is None:
        _vertex_client = genai.Client(
            vertexai=False,
            api_key="DummyAPIKey",
            http_options=types.HttpOptions(base_url="http://10.160.0.6:8000")
        )
    return _vertex_client


def _ensure_places_auth_objects() -> Tuple[Any, Any]:
    global _places_credentials, _places_auth_req
    if _places_credentials is None or _places_auth_req is None:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _places_credentials = creds
        _places_auth_req = google.auth.transport.requests.Request()
    return _places_credentials, _places_auth_req


async def _get_places_bearer_token() -> str:
    async with _places_lock:
        creds, auth_req = _ensure_places_auth_objects()
        if not creds.valid or not creds.token:
            await asyncio.to_thread(creds.refresh, auth_req)
        return creds.token


async def _places_headers(field_mask: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "X-Goog-Api-Key": os.getenv("GOOGLE_API_KEY"),
        "Content-Type": "application/json",
    }
    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask
    return headers


# =============================================================================
# HELPERS
# =============================================================================

def _sanitize(text: str) -> str:
    return (text or "").replace("\xa0", " ").strip()


def _safe_json_loads(text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}

    parsed = _safe_json_loads(raw)
    if parsed:
        return parsed

    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[len("```json"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    parsed = _safe_json_loads(cleaned)
    if parsed:
        return parsed

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        parsed = _safe_json_loads(candidate)
        if parsed:
            return parsed

    applog.error(f"Could not parse model output as JSON. Raw text: {raw[:2000]}")
    return {}


def _log_tool_call(name: str, payload: Dict[str, Any]) -> None:
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call(name, json.dumps(payload))


def _log_tool_output(name: str, payload: Dict[str, Any]) -> None:
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call_output(name, json.dumps(payload))


def _normal_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    )


def _call_llm_json_sync(prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
    client = _get_vertex_client()

    full_prompt = f"""
Return ONLY valid JSON.
Do not wrap in markdown.
Do not add explanation text.
Do not add any text before or after JSON.

JSON schema guidance:
{json.dumps(schema.model_json_schema(), ensure_ascii=False)}

Task:
{prompt}
""".strip()

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=full_prompt,
        config=_normal_config(),
    )

    raw_text = getattr(response, "text", "") or ""
    parsed = _extract_json_object(raw_text)

    return {
        "data": parsed,
        "sources": [],
        "grounding_queries": [],
        "model": GEMINI_MODEL,
        "raw_text": raw_text,
    }


async def _call_llm_json(prompt: str, schema: Type[BaseModel]) -> Dict[str, Any]:
    return await asyncio.to_thread(_call_llm_json_sync, prompt, schema)


# =============================================================================
# PLACES IMAGE HELPERS
# =============================================================================

async def _places_text_search(query: str, max_result_count: int = 1) -> Dict[str, Any]:
    headers = await _places_headers(
        field_mask="places.id,places.name,places.displayName,places.photos,places.rating,places.formattedAddress,places.priceLevel,places.primaryType"
    )
    payload = {
        "textQuery": query,
        "maxResultCount": max_result_count,
    }

    async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
        resp = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            applog.error(f"Places search failed {resp.status_code}: {resp.text[:500]}")
            return {}
        return resp.json()


async def _get_place_image_url(query: str, max_height_px: int = 900) -> str:
    try:
        data = await _places_text_search(query, max_result_count=1)
        places = data.get("places") or []
        if not places:
            return ""

        photos = places[0].get("photos") or []
        if not photos:
            return ""

        photo_name = photos[0].get("name", "")
        if not photo_name:
            return ""

        token = await _get_places_bearer_token()
        media_url = f"https://places.googleapis.com/v1/{photo_name}/media?maxHeightPx={max_height_px}"

        async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT, follow_redirects=False) as client:
            resp = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code in (301, 302, 303, 307, 308):
                return resp.headers.get("Location", "") or ""

        return ""
    except Exception:
        applog.error("Failed fetching place image URL:\n" + traceback.format_exc())
        return ""


async def _enrich_named_items_with_images(
    destination: str,
    items: List[Dict[str, Any]],
    name_key: str = "name",
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in items:
        row = dict(item)
        name = _sanitize(row.get(name_key, ""))
        row["image_url"] = await _get_place_image_url(f"{name}, {destination}") if name else ""
        enriched.append(row)
    return enriched


async def _enrich_places_with_images(destination: str, places: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return await _enrich_named_items_with_images(destination, places, "name")


async def _enrich_foods_with_images(destination: str, foods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return await _enrich_named_items_with_images(destination, foods, "name")


async def _enrich_activities_with_images(destination: str, activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return await _enrich_named_items_with_images(destination, activities, "name")


async def _enrich_sightseeing_with_images(destination: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return await _enrich_named_items_with_images(destination, items, "name")


async def _enrich_destinations_with_images(destinations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for item in destinations:
        row = dict(item)
        name = _sanitize(row.get("name", ""))
        row["image_url"] = await _get_place_image_url(name) if name else ""
        enriched.append(row)
    return enriched


async def _search_hotels_with_places(destination: str, custom_user_request_input_as_string: str, limit: int = 4) -> List[Dict[str, Any]]:
    query = f"best hotels in {destination}"
    if custom_user_request_input_as_string:
        query = f"{custom_user_request_input_as_string} hotels in {destination}"

    try:
        data = await _places_text_search(query, max_result_count=limit)
        places = data.get("places") or []
        hotels: List[Dict[str, Any]] = []

        for place in places[:limit]:
            name = _sanitize(((place.get("displayName") or {}).get("text")) or "")
            rating = place.get("rating")
            address = _sanitize(place.get("formattedAddress") or "")
            primary_type = _sanitize(place.get("primaryType") or "hotel")

            image_url = ""
            photos = place.get("photos") or []
            if photos:
                photo_name = photos[0].get("name", "")
                if photo_name:
                    token = await _get_places_bearer_token()
                    media_url = f"https://places.googleapis.com/v1/{photo_name}/media?maxHeightPx=900"
                    async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT, follow_redirects=False) as client:
                        resp = await client.get(
                            media_url,
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        if resp.status_code in (301, 302, 303, 307, 308):
                            image_url = resp.headers.get("Location", "") or ""

            hotels.append({
                "name": name,
                "description": f"{primary_type.title()} in {destination}",
                "rating": float(rating) if rating is not None else None,
                "address": address,
                "image_url": image_url,
            })

        return hotels
    except Exception:
        applog.error("Failed hotel search:\n" + traceback.format_exc())
        return []


# =============================================================================
# SCHEMAS
# =============================================================================

class PlaceItem(BaseModel):
    name: str
    description: str
    best_for: List[str]
    image_url: str = ""


class DestinationInfoResult(BaseModel):
    destination: str
    short_overview: str
    best_time_to_visit: str
    top_places_to_visit: List[PlaceItem]
    hero_image_url: str = ""
    practical_highlights: List[str]


class WeatherMonthResult(BaseModel):
    destination: str
    month: str
    weather_type: Literal[
        "sunny",
        "partly_cloudy",
        "cloudy",
        "rainy",
        "stormy",
        "snowy",
        "windy",
        "humid",
        "cold",
        "hot",
        "mixed"
    ]
    weather_summary: str
    average_temperature_range: str
    humidity_or_rain_context: str
    what_to_pack: List[str]
    travel_advice: List[str]
    suitable_activities: List[str]


class FoodItem(BaseModel):
    name: str
    description: str
    where_to_try: str
    dietary_note: str
    image_url: str = ""


class DestinationFoodResult(BaseModel):
    destination: str
    food_overview: str
    must_try_foods: List[FoodItem]


class DestinationEssentialsResult(BaseModel):
    destination: str
    currency: str
    local_transport: List[str]
    language_tips: List[str]
    safety_tips: List[str]
    etiquette_tips: List[str]
    connectivity_tips: List[str]


class ItineraryStop(BaseModel):
    day: int
    title: str
    summary: str
    key_places: List[str]


class DestinationItineraryResult(BaseModel):
    destination: str
    days: int
    trip_style: str
    itinerary: List[ItineraryStop]
    notes: List[str]


class DestinationBudgetResult(BaseModel):
    destination: str
    trip_style: str
    estimated_daily_budget: str
    budget_breakdown: List[str]
    money_saving_tips: List[str]


class SightseeingItem(BaseModel):
    name: str
    description: str
    ideal_duration: str
    best_time_to_visit: str
    image_url: str = ""


class DestinationSightseeingResult(BaseModel):
    destination: str
    sightseeing_overview: str
    top_sightseeing: List[SightseeingItem]


class ActivityItem(BaseModel):
    name: str
    description: str
    category: Literal[
        "adventure",
        "nature",
        "culture",
        "family",
        "relaxation",
        "nightlife",
        "shopping",
        "food",
        "water",
        "wildlife",
        "romantic",
        "seasonal",
        "general"
    ]
    ideal_duration: str
    image_url: str = ""


class DestinationActivitiesResult(BaseModel):
    destination: str
    activities_overview: str
    top_activities: List[ActivityItem]


class VisaInfoResult(BaseModel):
    destination: str
    visa_required: Literal["yes", "no", "depends"]
    visa_type: str
    processing_time: str
    validity_info: str
    stay_duration_info: str
    documents_required: List[str]
    visa_notes: List[str]
    friendly_summary: str = ""


class RecommendedDestinationItem(BaseModel):
    name: str
    description: str
    best_for: List[str]
    image_url: str = ""


class RecommendDestinationsResult(BaseModel):
    custom_user_request_input_as_string: str
    recommended_destinations: List[RecommendedDestinationItem]


class HotelItem(BaseModel):
    name: str
    description: str
    rating: Optional[float] = None
    address: str = ""
    image_url: str = ""


class DestinationHotelsResult(BaseModel):
    destination: str
    custom_user_request_input_as_string: str
    hotel_overview: str
    hotels: List[HotelItem]


_destination_cache = {}

def _check_dest_cache(tool_name: str, key: str):
    cached = _destination_cache.get((tool_name, key.strip().lower()))
    if cached is not None:
        from app_logger import applog
        applog.info(f"[CACHE HIT] {tool_name}('{key}')")
    return cached

def _store_dest_cache(tool_name: str, key: str, result: dict):
    from app_logger import applog
    _destination_cache[(tool_name, key.strip().lower())] = result
    applog.info(f"[CACHE STORE] {tool_name}('{key}')")

# =============================================================================
# TOOLS
# =============================================================================

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def recommend_destinations(
    context: RunContext,  # type: ignore
    custom_user_request_input_as_string: str,
) -> Dict[str, Any]:
    payload = {"custom_user_request_input_as_string": custom_user_request_input_as_string}
    _log_tool_call("recommend_destinations", payload)

    cached = _check_dest_cache("recommend_destinations", custom_user_request_input_as_string)
    if cached is not None:
        return cached

    try:
        custom_user_request_input_as_string = _sanitize(custom_user_request_input_as_string)
        if not custom_user_request_input_as_string:
            result = {"message": "Custom input is required.", "code": 400}
            _log_tool_output("recommend_destinations", result)
            return result

        prompt = f"""
You are a travel recommendation assistant.
Recommend destinations based on this user preference: {custom_user_request_input_as_string}

Requirements:
- Return valid JSON only.
- Recommend 2 to 4 destination names.
- If the user asks for easy international travel from India or doesn't specify a location, ALWAYS include destinations like Bali, Dubai, Vietnam, or Singapore as they are very easy and popular for Indians.
- For each destination include:
  - name
  - description
  - best_for
- Keep descriptions short and clear.
"""

        llm_result = await _call_llm_json(prompt, RecommendDestinationsResult)
        data = llm_result.get("data") or {}

        if not isinstance(data, dict) or not data:
            result = {
                "message": "Destination recommendations could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "code": 502,
            }
            _log_tool_output("recommend_destinations", result)
            return result

        recs = data.get("recommended_destinations") or []
        if isinstance(recs, list):
            data["recommended_destinations"] = await _enrich_destinations_with_images(recs[:4])

        result = {
            "custom_user_request_input_as_string": custom_user_request_input_as_string,
            "recommendations": data,
            "code": 200,
        }
        _log_tool_output("recommend_destinations", result)
        _store_dest_cache("recommend_destinations", custom_user_request_input_as_string, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in recommend_destinations: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("recommend_destinations", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_hotels(
    context: RunContext,  # type: ignore
    destination: str,
    custom_user_request_input_as_string: str = "",
) -> Dict[str, Any]:
    payload = {"destination": destination, "custom_user_request_input_as_string": custom_user_request_input_as_string}
    _log_tool_call("get_destination_hotels", payload)

    cached = _check_dest_cache("get_destination_hotels", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        custom_user_request_input_as_string = _sanitize(custom_user_request_input_as_string)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_hotels", result)
            return result

        prompt = f"""
You are a travel hotel assistant.
Give a short hotel overview for destination: {destination}
User preference: {custom_user_request_input_as_string if custom_user_request_input_as_string else "general"}

Requirements:
- Return valid JSON only.
- Include hotel_overview only.
- hotels can be an empty list because actual hotels will be filled from Google Places.
"""

        llm_result = await _call_llm_json(prompt, DestinationHotelsResult)
        data = llm_result.get("data") or {}

        if not isinstance(data, dict):
            data = {}

        hotels = await _search_hotels_with_places(destination, custom_user_request_input_as_string, limit=4)
        data["destination"] = destination
        data["custom_user_request_input_as_string"] = custom_user_request_input_as_string
        data["hotel_overview"] = data.get("hotel_overview") or f"Hotel options for {destination} based on your preference."
        data["hotels"] = hotels

        result = {
            "destination": destination,
            "hotels": data,
            "code": 200,
        }
        _log_tool_output("get_destination_hotels", result)
        _store_dest_cache("get_destination_hotels", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_hotels: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_hotels", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_info(
    context: RunContext,  # type: ignore
    destination: str,
) -> Dict[str, Any]:
    payload = {"destination": destination}
    _log_tool_call("get_destination_info", payload)

    cached = _check_dest_cache("get_destination_info", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_info", result)
            return result

        prompt = f"""
You are a travel research assistant.
Return concise destination information for: {destination}.

Requirements:
- Return valid JSON only.
- Keep descriptions concise and useful for a travel voice/chat assistant.
- Include exactly 4 top places only.
- For each place include: name, description, best_for.
- Keep best_time_to_visit short.
- Include practical_highlights as short bullet-style strings.
"""

        llm_result = await _call_llm_json(prompt, DestinationInfoResult)
        info = llm_result.get("data") or {}

        if not isinstance(info, dict) or not info:
            result = {
                "message": "Destination info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_info", result)
            return result

        top_places = info.get("top_places_to_visit") or []
        if isinstance(top_places, list):
            info["top_places_to_visit"] = await _enrich_places_with_images(destination, top_places[:4])

        info["hero_image_url"] = await _get_place_image_url(destination)

        result = {
            "destination": destination,
            "info": info,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_info", result)
        _store_dest_cache("get_destination_info", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_info: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_info", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_weather(
    context: RunContext,  # type: ignore
    destination: str,
    month: str,
) -> Dict[str, Any]:
    payload = {"destination": destination, "month": month}
    _log_tool_call("get_destination_weather", payload)

    cached = _check_dest_cache("get_destination_weather", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        month = _sanitize(month)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_weather", result)
            return result
        if not month:
            result = {"message": "Month is required.", "code": 400}
            _log_tool_output("get_destination_weather", result)
            return result

        prompt = f"""
You are a travel weather assistant.
Provide practical month-specific travel weather guidance for {destination} in {month}.

Requirements:
- Return valid JSON only.
- Select exactly one weather_type from this fixed enum list only:
  sunny, partly_cloudy, cloudy, rainy, stormy, snowy, windy, humid, cold, hot, mixed
- weather_type must be chosen for UI card rendering.
- Keep output short and useful for travelers.
- Include average_temperature_range as a human-friendly string.
- Include what_to_pack, travel_advice, and suitable_activities as short lists.
"""

        llm_result = await _call_llm_json(prompt, WeatherMonthResult)
        weather = llm_result.get("data") or {}

        if not isinstance(weather, dict) or not weather:
            result = {
                "message": "Weather info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_weather", result)
            return result

        result = {
            "destination": destination,
            "month": month,
            "weather": weather,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_weather", result)
        _store_dest_cache("get_destination_weather", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_weather: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_weather", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_food(
    context: RunContext,  # type: ignore
    destination: str,
    custom_user_request_input_as_string: str = "",
) -> Dict[str, Any]:
    payload = {"destination": destination, "custom_user_request_input_as_string": custom_user_request_input_as_string}
    _log_tool_call("get_destination_food", payload)

    cached = _check_dest_cache("get_destination_food", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        custom_user_request_input_as_string = _sanitize(custom_user_request_input_as_string)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_food", result)
            return result

        prompt = f"""
You are a travel food assistant.
List must-try foods for {destination}.
User preference: {custom_user_request_input_as_string if custom_user_request_input_as_string else "general"}

Requirements:
- Return valid JSON only.
- Include exactly 3 must-try foods.
- Adapt to user preference if provided.
- For each dish include:
  - name
  - description
  - where_to_try
  - dietary_note
- Keep descriptions short and useful.
"""

        llm_result = await _call_llm_json(prompt, DestinationFoodResult)
        food = llm_result.get("data") or {}

        if not isinstance(food, dict) or not food:
            result = {
                "message": "Food info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_food", result)
            return result

        foods = food.get("must_try_foods") or []
        if isinstance(foods, list):
            food["must_try_foods"] = await _enrich_foods_with_images(destination, foods[:3])

        result = {
            "destination": destination,
            "food": food,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_food", result)
        _store_dest_cache("get_destination_food", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_food: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_food", result)
        return result



@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_budget_guide(
    context: RunContext,  # type: ignore
    destination: str,
    trip_style: str = "mid-range",
) -> Dict[str, Any]:
    payload = {"destination": destination, "trip_style": trip_style}
    _log_tool_call("get_destination_budget_guide", payload)

    try:
        destination = _sanitize(destination)
        trip_style = _sanitize(trip_style or "mid-range")

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_budget_guide", result)
            return result

        prompt = f"""
You are a travel budgeting assistant.
Provide a daily budget guide for {destination}.
Trip style: {trip_style}.

Requirements:
- Return valid JSON only.
- Give a human-friendly daily budget estimate.
- Include typical budget buckets like stay, food, local transport, tickets.
- Add a few money-saving tips.
- Keep it practical for travelers.
"""

        llm_result = await _call_llm_json(prompt, DestinationBudgetResult)
        budget_guide = llm_result.get("data") or {}

        if not isinstance(budget_guide, dict) or not budget_guide:
            result = {
                "message": "Budget guide could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_budget_guide", result)
            return result

        result = {
            "destination": destination,
            "trip_style": trip_style,
            "budget_guide": budget_guide,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_budget_guide", result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_budget_guide: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_budget_guide", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_sightseeing(
    context: RunContext,  # type: ignore
    destination: str,
    custom_user_request_input_as_string: str = "",
) -> Dict[str, Any]:
    payload = {"destination": destination, "custom_user_request_input_as_string": custom_user_request_input_as_string}
    _log_tool_call("get_destination_sightseeing", payload)

    cached = _check_dest_cache("get_destination_sightseeing", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        custom_user_request_input_as_string = _sanitize(custom_user_request_input_as_string)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_sightseeing", result)
            return result

        prompt = f"""
You are a travel sightseeing assistant.
Provide top sightseeing recommendations for {destination}.
User preference: {custom_user_request_input_as_string if custom_user_request_input_as_string else "general"}

Requirements:
- Return valid JSON only.
- Include exactly 4 sightseeing spots.
- Adapt to user preference if provided.
- For each item include:
  - name
  - description
  - ideal_duration
  - best_time_to_visit
- Keep descriptions short and UI-friendly.
"""

        llm_result = await _call_llm_json(prompt, DestinationSightseeingResult)
        sightseeing = llm_result.get("data") or {}

        if not isinstance(sightseeing, dict) or not sightseeing:
            result = {
                "message": "Sightseeing info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_sightseeing", result)
            return result

        items = sightseeing.get("top_sightseeing") or []
        if isinstance(items, list):
            sightseeing["top_sightseeing"] = await _enrich_sightseeing_with_images(destination, items[:4])

        result = {
            "destination": destination,
            "sightseeing": sightseeing,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_sightseeing", result)
        _store_dest_cache("get_destination_sightseeing", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_sightseeing: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_sightseeing", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_activities(
    context: RunContext,  # type: ignore
    destination: str,
    custom_user_request_input_as_string: str = "",
) -> Dict[str, Any]:
    payload = {"destination": destination, "custom_user_request_input_as_string": custom_user_request_input_as_string}
    _log_tool_call("get_destination_activities", payload)

    cached = _check_dest_cache("get_destination_activities", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        custom_user_request_input_as_string = _sanitize(custom_user_request_input_as_string)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_activities", result)
            return result

        prompt = f"""
You are a travel activities assistant.
Provide top activities for {destination}.
User preference: {custom_user_request_input_as_string if custom_user_request_input_as_string else "general"}

Requirements:
- Return valid JSON only.
- Include exactly 4 activities.
- Adapt to user preference if provided.
- Each activity must include:
  - name
  - description
  - category
  - ideal_duration
- category must be one of:
  adventure, nature, culture, family, relaxation, nightlife, shopping, food, water, wildlife, romantic, seasonal, general
- Keep descriptions short and useful.
"""

        llm_result = await _call_llm_json(prompt, DestinationActivitiesResult)
        activities = llm_result.get("data") or {}

        if not isinstance(activities, dict) or not activities:
            result = {
                "message": "Activities info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_activities", result)
            return result

        items = activities.get("top_activities") or []
        if isinstance(items, list):
            activities["top_activities"] = await _enrich_activities_with_images(destination, items[:4])

        result = {
            "destination": destination,
            "activities": activities,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_activities", result)
        _store_dest_cache("get_destination_activities", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_activities: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_activities", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_visa_info(
    context: RunContext,  # type: ignore
    destination: str,
) -> Dict[str, Any]:
    payload = {"destination": destination}
    _log_tool_call("get_destination_visa_info", payload)

    cached = _check_dest_cache("get_destination_visa_info", destination)
    if cached is not None:
        return cached

    try:
        destination = _sanitize(destination)
        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("get_destination_visa_info", result)
            return result

        prompt = f"""
You are a travel visa assistant specializing in Indian passport holder travel requirements.
Provide accurate visa information for Indian citizens visiting {destination}.

CRITICAL RULES:
- If {destination} is visa-free for Indian passport holders, set visa_required to "no" and visa_type to "Visa Free".
- If {destination} offers visa-on-arrival for Indian passport holders, set visa_required to "no" and visa_type to "Visa on Arrival".
- If {destination} offers e-visa for Indian passport holders, set visa_required to "depends" and visa_type to "e-Visa".
- If {destination} requires a regular embassy/consulate visa, set visa_required to "yes" and visa_type to the specific visa type (e.g. "Tourist Visa", "Schengen Visa").

Requirements:
- Return valid JSON only.
- Keep it concise and practical.
- Include:
  - visa_required: one of yes, no, depends
  - visa_type: "Visa Free", "Visa on Arrival", "e-Visa", or specific visa type
  - processing_time: processing time or "Not applicable" for visa-free/VOA
  - validity_info
  - stay_duration_info: how long Indian citizens can stay
  - documents_required: practical list of standard items needed
  - visa_notes: short cautionary points, include any specific conditions for Indian passport holders
  - friendly_summary: a one-line friendly summary like "Thailand offers Visa on Arrival for Indian citizens for up to 15 days!" or "Maldives is visa-free for Indians — just pack your bags and go!"
- documents_required should be a practical list of standard items.
- visa_notes should include short cautionary points specific to Indian travelers.
- Do not mention that this is legal advice.
"""

        llm_result = await _call_llm_json(prompt, VisaInfoResult)
        visa = llm_result.get("data") or {}

        if not isinstance(visa, dict) or not visa:
            result = {
                "message": "Visa info could not be parsed.",
                "raw_text": llm_result.get("raw_text", ""),
                "sources": [],
                "grounding_queries": [],
                "code": 502,
            }
            _log_tool_output("get_destination_visa_info", result)
            return result

        result = {
            "destination": destination,
            "visa": visa,
            "sources": [],
            "grounding_queries": [],
            "code": 200,
        }
        _log_tool_output("get_destination_visa_info", result)
        _store_dest_cache("get_destination_visa_info", destination, result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_visa_info: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_visa_info", result)
        return result


# =============================================================================
# FLIGHT SEARCH – CONFIG
# =============================================================================

FLIGHT_TOKEN_URL = "https://services.thomascook.in/tcCommonRS/extnrt/getNewRequestToken"
FLIGHT_SEARCH_URL = "https://services.thomascook.in/tcFlightRS/webresources/searchsrp/oneway?flag=false"

FLIGHT_TOKEN_HEADERS = {
    "uniqueId": "2402:3a80:65f:fff:558a:142:b1ad:ac27",
    "user": "pathfndr",
}

FLIGHT_STATIC_BODY: Dict[str, Any] = {
    "gstCustomerStateCode": "27",
    "gstCustStateIsUT": "N",
    "stateName": "Andhra Pradesh",
    "classoftravel": "E",
    "uniqueKey": "06a418e8-dd38-4d71-a220-99a244706716",
    "regularFare": True,
}

FLIGHT_IMAGES_BASE_URL = "https://resources.thomascook.in/images/flight/airline"


# =============================================================================
# FLIGHT SEARCH – SCHEMAS
# =============================================================================

class FlightPreference(BaseModel):
    require_non_stop: bool = False
    sort_by: Literal[
        "shortest",
        "cheapest",
        "earliest_departure",
        "latest_departure",
        "recommended",
        "best",
    ] = "shortest"
    max_results: int = 10


# =============================================================================
# FLIGHT SEARCH – HELPERS
# =============================================================================

def _safe_fare_str(val: Any) -> str:
    """Convert fare value (may be float like 11460.0) to clean integer string.
    
    The API returns floats; naive str() gives '11460.0' which when parsed
    by frontend strips the dot and merges digits, causing 10x inflation.
    """
    if val is None:
        return "0"
    try:
        return str(int(float(str(val))))
    except (ValueError, TypeError):
        return "0"


class _IATACodeResult(BaseModel):
    iata_code: str = Field(description="The 3-letter IATA airport code for the given city")


async def _resolve_city_to_iata(city_input: str) -> str:
    """Convert a city name or IATA code to a valid IATA code using the LLM.
    
    If it looks like it's already an IATA code (3 uppercase letters), return as-is.
    Otherwise ask the model to resolve it.
    """
    cleaned = (city_input or "").strip()
    if not cleaned:
        return ""
    
    # Already an IATA code?
    upper = cleaned.upper()
    if len(upper) == 3 and upper.isalpha():
        return upper
    
    # Use model to resolve city name → IATA code
    try:
        prompt = f"""Return the primary IATA airport code for this city/location: "{cleaned}"
Rules:
- Return ONLY the 3-letter IATA code.
- Use the main/primary international airport if a city has multiple airports.
- Examples: Mumbai → BOM, Dubai → DXB, New York → JFK, London → LHR, Hyderabad → HYD, Goa → GOI
"""
        llm_result = await _call_llm_json(prompt, _IATACodeResult)
        data = llm_result.get("data") or {}
        code = (data.get("iata_code") or "").strip().upper()
        if len(code) == 3 and code.isalpha():
            applog.info(f"Resolved city '{cleaned}' → IATA code '{code}'")
            return code
    except Exception:
        applog.error(f"Failed to resolve city '{cleaned}' to IATA code:\n{traceback.format_exc()}")
    
    # Fallback: return uppercased input
    return upper



def _normalize_trip(trip: str) -> str:
    """Normalize trip type to 'dom' or 'intl'."""
    val = (trip or "").strip().lower()
    if val in ("dom", "domestic"):
        return "dom"
    return "intl"


def _normalize_depart_date(date_str: str) -> str:
    """Convert dd-mm-yyyy → d-mm-yyyy (strip leading zero on day) for API."""
    raw = (date_str or "").strip()
    if not raw:
        return raw
    parts = raw.split("-")
    if len(parts) == 3:
        day = parts[0].lstrip("0") or "1"
        return f"{day}-{parts[1]}-{parts[2]}"
    return raw


def _extract_digits(s: str) -> str:
    """Return only digit characters from a string."""
    return re.sub(r"[^0-9]", "", s or "")


def _duration_to_minutes(dur_str: str) -> int:
    """Parse '0:13:30' or '04:00' style duration into total minutes.

    Formats:
      '0:HH:MM' → HH*60 + MM
      'HH:MM'   → HH*60 + MM
    """
    raw = (dur_str or "").strip()
    if not raw:
        return 0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            # '0:HH:MM'
            hours = int(parts[1])
            minutes = int(parts[2])
            return hours * 60 + minutes
        elif len(parts) == 2:
            # 'HH:MM'
            hours = int(parts[0])
            minutes = int(parts[1])
            return hours * 60 + minutes
    except (ValueError, IndexError):
        pass
    return 0


def _minutes_to_text(mins: int) -> str:
    """Convert minutes to human text, e.g. 240 → '4hr 0m'."""
    if mins <= 0:
        return "0hr 0m"
    h = mins // 60
    m = mins % 60
    return f"{h}hr {m}m"


def _extract_time_part(dt_str: str) -> str:
    """'01-04-2026T14:25:00' → '14:25'."""
    if "T" in (dt_str or ""):
        time_section = dt_str.split("T")[1]
        return time_section[:5]
    return ""


def _extract_date_part(dt_str: str) -> str:
    """'01-04-2026T14:25:00' → '01-04-2026'."""
    if "T" in (dt_str or ""):
        return dt_str.split("T")[0]
    return dt_str or ""


def _airport_code_and_name(raw: str) -> Tuple[str, str]:
    """Parse 'HYD_Rajiv Gandhi International, Hyderabad_IN' →
    ('HYD', 'Rajiv Gandhi International, Hyderabad').
    """
    raw = (raw or "").strip()
    if not raw:
        return ("", "")
    parts = raw.split("_", 1)
    code = parts[0].strip()
    if len(parts) < 2:
        return (code, "")
    remainder = parts[1].strip()
    # remove trailing country code after last '_'
    last_under = remainder.rfind("_")
    if last_under != -1:
        name = remainder[:last_under].strip()
    else:
        name = remainder
    return (code, name)


async def _classify_flight_preference(custom_input: str) -> Dict[str, Any]:
    """Use LLM to convert free-text custom_input into structured preference.

    Returns default preference when custom_input is empty.
    """
    default_pref = {
        "require_non_stop": False,
        "sort_by": "shortest",
        "max_results": 10,
    }

    custom_input = _sanitize(custom_input)
    if not custom_input:
        return default_pref

    prompt = f"""
You are a flight search preference classifier.
Convert this user preference into structured JSON: "{custom_input}"

Rules:
- require_non_stop: true if user wants non-stop / direct flights, false otherwise
- sort_by: one of shortest, cheapest, earliest_departure, latest_departure, recommended, best
  - "cheapest flight" → cheapest
  - "shortest flight" → shortest
  - "non stop flight" → shortest (with require_non_stop true)
  - "best flight" → best
  - "recommended flight" → recommended
  - "early morning flight" → earliest_departure
  - "late departure" → latest_departure
  - "refundable fare" → best
  - "cheap and short" → cheapest
  - "direct and cheapest" → cheapest (with require_non_stop true)
- max_results: default 10

Return valid JSON only.
"""

    try:
        llm_result = await _call_llm_json(prompt, FlightPreference)
        data = llm_result.get("data") or {}
        if isinstance(data, dict) and data:
            return {
                "require_non_stop": data.get("require_non_stop", False),
                "sort_by": data.get("sort_by", "shortest"),
                "max_results": data.get("max_results", 10),
            }
    except Exception:
        applog.error("Flight preference classification failed:\n" + traceback.format_exc())

    return default_pref


def _parse_one_flight_option(flight_item: Dict[str, Any], images_url: str) -> Dict[str, Any]:
    """Parse one raw API flight dict into clean structured dict for UI."""

    # --- airline info ---
    airline_code = (flight_item.get("airlineCode") or "").strip()
    airline_name_raw = (flight_item.get("airlineName") or "").strip()
    # airlineName can be like "QR_Qatar Airways" or plain "Qatar Airways"
    if "_" in airline_name_raw:
        airline_name = airline_name_raw.split("_", 1)[1].strip()
    else:
        airline_name = airline_name_raw

    flight_number = f"{airline_code}-{flight_item.get('flightsID', '')}" if airline_code else ""
    airline_logo_url = f"{images_url}/{airline_code}.gif" if airline_code and images_url else ""
    is_recommended = str(flight_item.get("airlineRecommend", "")).upper() == "Y"
    is_non_stop = str(flight_item.get("nonStop", "")).upper() == "Y"
    no_of_seats = flight_item.get("noOfSeats", "")
    vendor = flight_item.get("vendor", "")

    # --- duration ---
    duration_list = flight_item.get("duration") or []
    dur_info = duration_list[0] if duration_list else {}
    total_dur_list = dur_info.get("totalDuration") or []
    total_dur_raw = total_dur_list[0] if total_dur_list else ""
    duration_minutes = _duration_to_minutes(total_dur_raw)
    duration_text = _minutes_to_text(duration_minutes)
    stop_count = dur_info.get("stops", 0)
    stops_text = "Non-stop" if stop_count == 0 else f"{stop_count} stop{'s' if stop_count > 1 else ''}"

    layover_dur_list = dur_info.get("totalLayoverDuration") or dur_info.get("layoverDuration") or []
    layover_raw = layover_dur_list[0] if layover_dur_list else ""
    layover_minutes = _duration_to_minutes(layover_raw)
    layover_text = _minutes_to_text(layover_minutes) if layover_minutes > 0 else "N/A"

    # --- fare ---
    fare_list = flight_item.get("fare") or []
    fare_info = fare_list[0] if fare_list else {}
    total_price = _safe_fare_str(flight_item.get("totalPrice") or fare_info.get("tp") or "0")
    base_fare = _safe_fare_str(fare_info.get("abp", "0"))
    tax_details = fare_info.get("taxDetails") or {}
    taxes = _safe_fare_str(tax_details.get("ttax", "0"))
    # airline_fare = base_fare + taxes (NOT atax which is just airline tax)
    try:
        airline_fare_val = int(base_fare) + int(taxes)
    except (ValueError, TypeError):
        airline_fare_val = 0
    airline_fare = str(airline_fare_val)
    fare_type_str = fare_info.get("fareType", "")
    fare_class_type = fare_info.get("fareClassType", "Economy")

    # commission / discount
    commission_bean = flight_item.get("comissionBean") or {}
    discount = _safe_fare_str(commission_bean.get("lessDiscount") or flight_item.get("lessDiscount") or "0")
    service_fee = _safe_fare_str(commission_bean.get("serviceTax") or flight_item.get("serviceTax") or "0")

    fare_basis_list = fare_info.get("fareBasisDetails") or []
    fare_refundable = ""
    for fb in fare_basis_list:
        ft = (fb.get("fareType") or "").strip()
        if ft:
            fare_refundable = ft
            break

    fare_details = {
        "base_fare": base_fare,
        "taxes_and_charges": taxes,
        "airline_fare": airline_fare,
        "discount": discount,
        "service_fee": service_fee,
        "total_fare": total_price,
        "fare_type": fare_refundable or fare_type_str,
        "fare_class_type": fare_class_type,
        "currency": "INR",
    }

    # --- segments ---
    raw_segments = flight_item.get("flight") or []
    segments: List[Dict[str, Any]] = []
    first_departure_time = ""
    first_departure_date = ""
    last_arrival_time = ""
    last_arrival_date = ""
    first_from_code = ""
    first_from_name = ""
    last_to_code = ""
    last_to_name = ""
    aircraft = ""
    operated_by = ""
    baggage_checkin_kg: Any = ""
    baggage_cabin = ""

    for idx, seg in enumerate(raw_segments):
        dep_airport_raw = seg.get("departureairport", "")
        arr_airport_raw = seg.get("arrivalairport", "")
        dep_code, dep_name = _airport_code_and_name(dep_airport_raw)
        arr_code, arr_name = _airport_code_and_name(arr_airport_raw)

        dep_time_raw = seg.get("departuretime", "")
        arr_time_raw = seg.get("arrivaltime", "")
        dep_time = _extract_time_part(dep_time_raw)
        arr_time = _extract_time_part(arr_time_raw)
        dep_date = _extract_date_part(dep_time_raw)
        arr_date = _extract_date_part(arr_time_raw)

        seg_duration = seg.get("flightDuration", "")
        seg_equip = seg.get("equipmentType", "")
        seg_fno = seg.get("fno", "")
        seg_baggage = seg.get("freeBaggageAllowedWeight", "")

        mac_raw = seg.get("mac", "")
        oac_name_raw = seg.get("oacName", "")
        seg_airline_code = mac_raw.split("_")[0].strip() if "_" in mac_raw else (seg.get("oac") or airline_code)
        seg_airline_name = mac_raw.split("_", 1)[1].strip() if "_" in mac_raw else airline_name

        seg_operated_raw = oac_name_raw.split(":", 1)[1].strip() if ":" in oac_name_raw else ""

        seg_flight_number = f"{seg_airline_code}-{seg_fno}" if seg_airline_code and seg_fno else ""

        segments.append({
            "airline_name": seg_airline_name,
            "airline_code": seg_airline_code,
            "flight_number": seg_flight_number,
            "from_airport_code": dep_code,
            "from_airport_name": dep_name,
            "to_airport_code": arr_code,
            "to_airport_name": arr_name,
            "departure_time": dep_time,
            "arrival_time": arr_time,
            "departure_date": dep_date,
            "arrival_date": arr_date,
            "duration_text": seg_duration,
            "aircraft": seg_equip,
            "operated_by": seg_operated_raw,
            "baggage_checkin_kg": seg_baggage,
        })

        if idx == 0:
            first_departure_time = dep_time
            first_departure_date = dep_date
            first_from_code = dep_code
            first_from_name = dep_name
            aircraft = seg_equip
            operated_by = seg_operated_raw or seg_airline_name
            baggage_checkin_kg = seg_baggage

        last_arrival_time = arr_time
        last_arrival_date = arr_date
        last_to_code = arr_code
        last_to_name = arr_name

    # --- departure datetime for sorting ---
    first_dep_dt_raw = ""
    if raw_segments:
        first_dep_dt_raw = raw_segments[0].get("departuretime", "")

    display_name = f"{airline_name} {flight_number}" if airline_name else flight_number

    # --- baggage details ---
    checkin_display = ""
    cabin_display = ""
    if baggage_checkin_kg:
        bkg = str(baggage_checkin_kg).strip()
        if bkg and bkg != "0" and bkg.lower() != "none":
            # Handle formats like "20", "20kg", "20-1PC", "1PC-20"
            weight_match = re.search(r'(\d+)', bkg)
            if weight_match:
                weight_val = weight_match.group(1)
                piece_match = re.search(r'(\d+)\s*PC', bkg.upper())
                if piece_match and piece_match.group(1) != weight_val:
                    pieces = piece_match.group(1)
                    checkin_display = f"{weight_val} kg ({pieces} piece)"
                else:
                    checkin_display = f"{weight_val} kg"
            else:
                checkin_display = bkg

    # Try to get cabin baggage from segment data
    if raw_segments:
        cabin_raw = raw_segments[0].get("cabinBaggage", "") or raw_segments[0].get("cabinBaggageAllowedWeight", "")
        if cabin_raw:
            cab = str(cabin_raw).strip()
            if cab and cab != "0" and cab.lower() != "none":
                cab_match = re.search(r'(\d+)', cab)
                if cab_match:
                    cabin_display = f"{cab_match.group(1)} kg (1 piece)"
                else:
                    cabin_display = cab

    # Default baggage when API doesn't provide it
    if not checkin_display:
        checkin_display = "As per airline"
    if not cabin_display:
        cabin_display = "7 kg (1 piece)"

    baggage_details = {
        "checkin": checkin_display,
        "cabin": cabin_display,
    }

    # --- cancellation details (derived from fare type) ---
    fare_type_lower = (fare_refundable or fare_type_str or "").strip().lower()
    if "non" in fare_type_lower and "refund" in fare_type_lower:
        cancellation_details = {
            "cancellation_fee": "Non-Refundable",
            "date_change_fee": "Non-Refundable",
            "thomas_cook_fee": "₹ 500",
        }
    elif "refund" in fare_type_lower:
        cancellation_details = {
            "cancellation_fee": "Cancellation charges as per airline policy",
            "date_change_fee": "Change charges as per airline policy",
            "thomas_cook_fee": "₹ 500",
        }
    else:
        cancellation_details = {
            "cancellation_fee": "As per airline policy",
            "date_change_fee": "As per airline policy",
            "thomas_cook_fee": "₹ 500",
        }

    return {
        "airline_name": airline_name,
        "airline_code": airline_code,
        "flight_number": flight_number,
        "display_name": display_name,
        "from_city": first_from_code,
        "to_city": last_to_code,
        "departure_time": first_departure_time,
        "arrival_time": last_arrival_time,
        "departure_date": first_departure_date,
        "arrival_date": last_arrival_date,
        "duration_text": duration_text,
        "duration_minutes": duration_minutes,
        "stop_count": stop_count,
        "stops_text": stops_text,
        "layover_text": layover_text,
        "price": str(total_price),
        "currency": "INR",
        "aircraft": aircraft,
        "cabin_class": fare_class_type,
        "operated_by": operated_by,
        "fare_type": fare_refundable or fare_type_str,
        "no_of_seats": str(no_of_seats),
        "vendor": str(vendor),
        "recommended": is_recommended,
        "airline_logo_url": airline_logo_url,
        "fare_details": fare_details,
        "baggage_details": baggage_details,
        "cancellation_details": cancellation_details,
        "segments": segments,

        # internal sort helpers (not for UI, but kept for sorting)
        "_is_non_stop": is_non_stop or stop_count == 0,
        "_departure_dt_raw": first_dep_dt_raw,
    }


def _extract_all_api_flights(api_json: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract all parsed flights from the API response.

    Returns (flights_list, meta_dict).
    """
    images_url = (api_json.get("imagesURL") or FLIGHT_IMAGES_BASE_URL).rstrip("/")

    meta = {
        "noofflights": api_json.get("noofflights", 0),
        "nonstopflights": api_json.get("nonstopflights") or api_json.get("nonStopflight", 0),
        "noOfRecommendedFlight": api_json.get("noOfRecommendedFlight", 0),
        "memcacheKey": api_json.get("memcacheKey", ""),
        "validFrom": api_json.get("validFrom", ""),
    }

    flights: List[Dict[str, Any]] = []
    all_flights = api_json.get("allFlights") or {}
    special_flights = all_flights.get("specialFlights") or {}

    for bucket_key, bucket_val in special_flights.items():
        if not isinstance(bucket_val, dict):
            continue
        onword_flights = bucket_val.get("onwordFlights") or []
        if not isinstance(onword_flights, list):
            continue
        for item in onword_flights:
            if not isinstance(item, dict):
                continue
            try:
                parsed = _parse_one_flight_option(item, images_url)
                flights.append(parsed)
            except Exception:
                applog.error(f"Failed parsing flight item in bucket '{bucket_key}':\n{traceback.format_exc()}")

    return flights, meta


def _sort_flights(
    flights: List[Dict[str, Any]],
    preference: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Sort and filter flights based on preference. Falls back gracefully."""

    require_non_stop = preference.get("require_non_stop", False)
    sort_by = preference.get("sort_by", "shortest")
    max_results = preference.get("max_results", 10)

    pool = list(flights)

    # filter non-stop if requested
    if require_non_stop:
        non_stop_pool = [f for f in pool if f.get("_is_non_stop")]
        if non_stop_pool:
            pool = non_stop_pool
        else:
            applog.info("No non-stop flights found; falling back to all flights sorted by shortest.")
            sort_by = "shortest"

    # sort
    if sort_by == "shortest":
        pool.sort(key=lambda f: f.get("duration_minutes", 9999))
    elif sort_by == "cheapest":
        pool.sort(key=lambda f: int(_extract_digits(str(f.get("price", "0"))) or "0"))
    elif sort_by == "earliest_departure":
        pool.sort(key=lambda f: f.get("_departure_dt_raw", ""))
    elif sort_by == "latest_departure":
        pool.sort(key=lambda f: f.get("_departure_dt_raw", ""), reverse=True)
    elif sort_by == "recommended":
        pool.sort(key=lambda f: (0 if f.get("recommended") else 1, f.get("duration_minutes", 9999)))
    elif sort_by == "best":
        # best = recommended first, then shortest among non-stop, then cheapest
        pool.sort(key=lambda f: (
            0 if f.get("recommended") else 1,
            0 if f.get("_is_non_stop") else 1,
            f.get("duration_minutes", 9999),
            int(_extract_digits(str(f.get("price", "0"))) or "0"),
        ))
    else:
        pool.sort(key=lambda f: f.get("duration_minutes", 9999))

    # strip internal sort helpers before returning
    results: List[Dict[str, Any]] = []
    for f in pool[:max_results]:
        clean = {k: v for k, v in f.items() if not k.startswith("_")}
        results.append(clean)

    return results


async def _fetch_flight_auth() -> Tuple[str, str, Dict[str, str]]:
    """Call token API, return (requestId, tokenId, cookies). Raises on failure."""
    headers = {
        **FLIGHT_TOKEN_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT) as client:
        resp = await client.get(FLIGHT_TOKEN_URL, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Flight token API returned {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        error_code = data.get("errorCode", -1)
        if error_code != 0:
            raise RuntimeError(f"Flight token API error: {data.get('errorMsg', 'unknown')}")
        request_id = data.get("requestId", "")
        token_id = data.get("tokenId", "")
        if not request_id or not token_id:
            raise RuntimeError("Flight token API returned empty requestId or tokenId")
        # capture cookies set by WAF (e.g. sess_map)
        cookies = dict(resp.cookies)
        return request_id, token_id, cookies


async def _fetch_flights_raw(
    request_id: str,
    session_id: str,
    body: Dict[str, Any],
    cookies: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """POST to flight search API, return raw JSON response dict."""
    headers = {
        "requestId": request_id,
        "sessionId": session_id,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=CUSTOM_HTTP_TIMEOUT, cookies=cookies) as client:
        resp = await client.post(FLIGHT_SEARCH_URL, headers=headers, json=body)
        if resp.status_code == 204:
            # 204 No Content = no flights found for this route/date
            applog.info("Flight search API returned 204 (no flights found)")
            return {}
        if resp.status_code != 200:
            raise RuntimeError(f"Flight search API returned {resp.status_code}: {resp.text[:500]}")
        return resp.json()


# =============================================================================
# FLIGHT SEARCH – TOOL
# =============================================================================

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def get_destination_flights(
    context: RunContext,  # type: ignore
    trip: Annotated[str, "Flight type: must be exactly 'dom' for domestic flights (both cities in same country, e.g. India to India) or 'intl' for international flights (different countries)"],
    adult: Annotated[int, "Number of adult passengers (12+ years)"],
    child: Annotated[int, "Number of child passengers (2-11 years)"],
    infant: Annotated[int, "Number of infant passengers (under 2 years)"],
    from_city: Annotated[str, "Departure city name or IATA airport code. Can be a city name like 'Hyderabad', 'Mumbai', 'Delhi' or an IATA code like 'HYD', 'BOM', 'DEL'. The system will automatically resolve city names to IATA codes."],
    to_city: Annotated[str, "Arrival city name or IATA airport code. Can be a city name like 'Dubai', 'Singapore', 'Bangkok' or an IATA code like 'DXB', 'SIN', 'BKK'. The system will automatically resolve city names to IATA codes."],
    depart: Annotated[str, "Departure date in dd-mm-yyyy format (e.g. '24-04-2026')"],
    custom_input: Annotated[str, "Optional user preference like 'cheapest', 'non-stop', 'early morning'"] = "",
) -> Dict[str, Any]:
    """
    Search for one-way flight options between two cities on a specific date.
    Returns real-time flight data including pricing, duration, airline, and baggage details.

    This tool should be used when the user asks for flight availability, prices, or recommendations 
    for a trip between two specific locations.

    Args:
        trip: Flight type: must be exactly 'dom' for domestic flights (both cities in same country, e.g. India to India) or 'intl' for international flights (different countries)
        adult: Number of adult passengers (12+ years)
        child: Number of child passengers (2-11 years)
        infant: Number of infant passengers (under 2 years)
        from_city: Departure city name or IATA airport code. Can be a city name like 'Hyderabad', 'Mumbai', 'Delhi' or an IATA code like 'HYD', 'BOM', 'DEL'. The system will automatically resolve city names to IATA codes.
        to_city: Arrival city name or IATA airport code. Can be a city name like 'Dubai', 'Singapore', 'Bangkok' or an IATA code like 'DXB', 'SIN', 'BKK'. The system will automatically resolve city names to IATA codes.
        depart: Departure date in dd-mm-yyyy format (e.g. '24-04-2026')
        custom_input: Optional user preference like 'cheapest', 'non-stop', 'early morning'
    """
    payload = {
        "trip": trip,
        "adult": adult,
        "child": child,
        "infant": infant,
        "from_city": from_city,
        "to_city": to_city,
        "depart": depart,
        "custom_input": custom_input,
    }
    _log_tool_call("get_destination_flights", payload)

    try:
        # ── validate inputs ──────────────────────────────────────────────
        from_city = await _resolve_city_to_iata(from_city)
        to_city = await _resolve_city_to_iata(to_city)
        trip = _normalize_trip(trip)
        depart = _sanitize(depart)
        custom_input = _sanitize(custom_input)
        
        if not from_city or not to_city:
            result = {"message": "from_city and to_city IATA codes are required.", "code": 400}
            _log_tool_output("get_destination_flights", result)
            return result

        if not depart:
            result = {"message": "depart date is required (dd-mm-yyyy).", "code": 400}
            _log_tool_output("get_destination_flights", result)
            return result

        adult = max(int(adult), 1)
        child = max(int(child), 0)
        infant = max(int(infant), 0)

        # ── classify preference ──────────────────────────────────────────
        applog.info(f"Classifying flight preference for custom_input='{custom_input}'")
        preference = await _classify_flight_preference(custom_input)
        applog.info(f"Flight preference resolved: {preference}")

        # ── fetch token ──────────────────────────────────────────────────
        applog.info("Fetching flight auth token …")
        request_id, session_id, auth_cookies = await _fetch_flight_auth()
        applog.info(f"Flight auth acquired: requestId={request_id}, cookies={len(auth_cookies)} keys")

        # ── build request body ───────────────────────────────────────────
        body: Dict[str, Any] = {
            **FLIGHT_STATIC_BODY,
            "trip": trip,
            "adult": adult,
            "child": child,
            "infant": infant,
            "fromCity": from_city.lower(),
            "toCity": to_city,
            "depart": _normalize_depart_date(depart),
        }

        # ── call flight search API ───────────────────────────────────────
        applog.info(f"Searching flights: {from_city} → {to_city} on {depart}")
        api_json = await _fetch_flights_raw(request_id, session_id, body, cookies=auth_cookies)

        # ── parse + sort ─────────────────────────────────────────────────
        all_flights, meta = _extract_all_api_flights(api_json)
        applog.info(f"Parsed {len(all_flights)} flights from API response")

        sorted_flights = _sort_flights(all_flights, preference)
        applog.info(f"Returning top {len(sorted_flights)} flights (sort_by={preference.get('sort_by')})")

        # ── structured return ────────────────────────────────────────────
        result: Dict[str, Any] = {
            "flight_results": {
                "trip": trip,
                "from_city": from_city,
                "to_city": to_city,
                "depart": depart,
                "custom_input": custom_input,
                "applied_preference": preference,
                "total_found": len(all_flights),
                "flights": sorted_flights,
            },
            "meta": meta,
            "code": 200,
        }
        _log_tool_output("get_destination_flights", result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in get_destination_flights: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("get_destination_flights", result)
        return result


# =============================================================================
# CUSTOM ITINERARY – SCHEMAS
# =============================================================================

class ItineraryFlightInfo(BaseModel):
    airline_name: str = ""
    flight_number: str = ""
    from_city: str = ""
    to_city: str = ""
    departure_time: str = ""
    arrival_time: str = ""
    duration_text: str = ""
    price: str = ""
    currency: str = "INR"
    stops_text: str = "Non-stop"
    airline_logo_url: str = ""


class ItineraryHotelInfo(BaseModel):
    name: str = ""
    star_rating: int = 3
    address: str = ""
    description: str = ""
    image_url: str = ""
    check_in: str = ""
    check_out: str = ""


class ItinerarySightseeingItem(BaseModel):
    name: str = ""
    description: str = ""
    ideal_duration: str = ""
    best_time: str = ""
    image_url: str = ""
    is_must_do: bool = False


class ItineraryActivityItem(BaseModel):
    name: str = ""
    description: str = ""
    category: str = "general"
    ideal_duration: str = ""
    image_url: str = ""
    is_must_do: bool = False


class ItineraryTransferItem(BaseModel):
    transfer_type: str = ""  # airport_pickup, airport_drop, intercity, local
    from_location: str = ""
    to_location: str = ""
    mode: str = ""  # cab, bus, train, ferry, walk
    estimated_duration: str = ""
    notes: str = ""


class ItineraryMealInfo(BaseModel):
    breakfast: str = ""
    lunch: str = ""
    dinner: str = ""


class ItineraryDayPlan(BaseModel):
    day: int = 1
    date: str = ""
    title: str = ""
    city: str = ""
    summary: str = ""
    flight: Optional[ItineraryFlightInfo] = None
    hotel: Optional[ItineraryHotelInfo] = None
    sightseeing: List[ItinerarySightseeingItem] = []
    activities: List[ItineraryActivityItem] = []
    transfers: List[ItineraryTransferItem] = []
    meals: Optional[ItineraryMealInfo] = None
    notes: str = ""


class CustomItineraryResult(BaseModel):
    title: str = ""
    destination: str = ""
    trip_type: str = ""  # domestic / international
    from_city: str = ""
    start_date: str = ""
    end_date: str = ""
    adults: int = 1
    children: int = 0
    infants: int = 0
    days: List[ItineraryDayPlan] = []
    total_estimated_budget: str = ""
    budget_breakdown: List[str] = []
    tips: List[str] = []
    hero_image_url: str = ""


class TripTypeResult(BaseModel):
    trip_type: str = "intl"


# =============================================================================
# CUSTOM ITINERARY – HELPERS
# =============================================================================

def _pick_best_flight(flights_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the single best flight from flight search results."""
    fr = flights_data.get("flight_results") or {}
    flights = fr.get("flights") or []
    if not flights:
        return {}
    top = flights[0]
    return {
        "airline_name": top.get("airline_name", ""),
        "flight_number": top.get("flight_number", ""),
        "from_city": top.get("from_city", ""),
        "to_city": top.get("to_city", ""),
        "departure_time": top.get("departure_time", ""),
        "arrival_time": top.get("arrival_time", ""),
        "duration_text": top.get("duration_text", ""),
        "price": top.get("price", ""),
        "currency": top.get("currency", "INR"),
        "stops_text": top.get("stops_text", "Non-stop"),
        "airline_logo_url": top.get("airline_logo_url", ""),
    }


def _pick_top_hotels(hotels_data: List[Dict[str, Any]], limit: int = 1) -> List[Dict[str, Any]]:
    """Pick top hotels from Places search results."""
    result = []
    for h in hotels_data[:limit]:
        result.append({
            "name": h.get("name", ""),
            "star_rating": 3,
            "address": h.get("address", ""),
            "description": h.get("description", ""),
            "image_url": h.get("image_url", ""),
        })
    return result


async def _enrich_itinerary_images(destination: str, days: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich sightseeing and activity images in itinerary days."""
    for day in days:
        city = day.get("city", destination)

        # Enrich sightseeing images
        sightseeing = day.get("sightseeing") or []
        for item in sightseeing:
            name = item.get("name", "")
            if name and not item.get("image_url"):
                item["image_url"] = await _get_place_image_url(f"{name}, {city}")

        # Enrich hotel image
        hotel = day.get("hotel")
        if hotel and hotel.get("name") and not hotel.get("image_url"):
            hotel["image_url"] = await _get_place_image_url(f"{hotel['name']}, {city}")

    return days


# =============================================================================
# CUSTOM ITINERARY – TOOLS
# =============================================================================

@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def create_custom_itinerary(
    context: RunContext,  # type: ignore
    destination: Annotated[str, "Travel destination city/country (e.g. 'Goa', 'Dubai', 'Thailand')"],
    from_city: Annotated[str, "Departure city name or IATA code (e.g. 'Hyderabad', 'Mumbai', 'DEL')"],
    start_date: Annotated[str, "Trip start date in dd-mm-yyyy format (e.g. '15-04-2026')"],
    end_date: Annotated[str, "Trip end date in dd-mm-yyyy format (e.g. '18-04-2026')"],
    adults: Annotated[int, "Number of adult travellers (12+ years)"] = 2,
    children: Annotated[int, "Number of children (2-11 years)"] = 0,
    infants: Annotated[int, "Number of infants (under 2 years)"] = 0,
    custom_request: Annotated[str, "Optional free-text preferences (e.g. 'luxury hotels', 'adventure activities', 'include scuba diving')"] = "",
) -> Dict[str, Any]:
    """
    Create a comprehensive day-wise custom travel itinerary with flights, hotels,
    sightseeing, activities, transfers, and meals.

    Defaults: 3-star hotels, best flights, must-do activities first, top sightseeing.
    Use this when the user wants a complete personalized itinerary built from scratch.
    """
    payload = {
        "destination": destination,
        "from_city": from_city,
        "start_date": start_date,
        "end_date": end_date,
        "adults": adults,
        "children": children,
        "infants": infants,
        "custom_request": custom_request,
    }
    _log_tool_call("create_custom_itinerary", payload)

    try:
        destination = _sanitize(destination)
        from_city_raw = _sanitize(from_city)
        start_date = _sanitize(start_date)
        end_date = _sanitize(end_date)
        custom_request = _sanitize(custom_request)
        adults = max(int(adults), 1)
        children = max(int(children), 0)
        infants = max(int(infants), 0)

        if not destination:
            result = {"message": "Destination is required.", "code": 400}
            _log_tool_output("create_custom_itinerary", result)
            return result

        if not start_date or not end_date:
            result = {"message": "start_date and end_date are required (dd-mm-yyyy).", "code": 400}
            _log_tool_output("create_custom_itinerary", result)
            return result

        # ── Determine trip type ──────────────────────────────────────────
        trip_type_prompt = f'Is travel from "{from_city_raw}" to "{destination}" domestic (same country) or international? Return JSON: {{"trip_type": "dom"}} or {{"trip_type": "intl"}}'
        trip_type_result = await _call_llm_json(trip_type_prompt, TripTypeResult)
        trip_type_data = trip_type_result.get("data") or {}
        trip_type = trip_type_data.get("trip_type", "intl")
        trip_type = _normalize_trip(trip_type)

        # ── Gather flight data (outbound + return) ───────────────────────
        applog.info(f"Itinerary: Searching outbound flight {from_city_raw} → {destination} on {start_date}")
        outbound_flight_data: Dict[str, Any] = {}
        return_flight_data: Dict[str, Any] = {}

        try:
            from_iata = await _resolve_city_to_iata(from_city_raw)
            to_iata = await _resolve_city_to_iata(destination)

            # Outbound flight
            request_id, session_id, auth_cookies = await _fetch_flight_auth()
            outbound_body: Dict[str, Any] = {
                **FLIGHT_STATIC_BODY,
                "trip": trip_type,
                "adult": adults,
                "child": children,
                "infant": infants,
                "fromCity": from_iata.lower(),
                "toCity": to_iata,
                "depart": _normalize_depart_date(start_date),
            }
            out_api = await _fetch_flights_raw(request_id, session_id, outbound_body, cookies=auth_cookies)
            out_flights, _ = _extract_all_api_flights(out_api)
            best_pref = {"require_non_stop": False, "sort_by": "best", "max_results": 1}
            sorted_out = _sort_flights(out_flights, best_pref)
            if sorted_out:
                outbound_flight_data = sorted_out[0]
            applog.info(f"Itinerary: Found {len(out_flights)} outbound flights")

            # Return flight
            request_id2, session_id2, auth_cookies2 = await _fetch_flight_auth()
            return_body: Dict[str, Any] = {
                **FLIGHT_STATIC_BODY,
                "trip": trip_type,
                "adult": adults,
                "child": children,
                "infant": infants,
                "fromCity": to_iata.lower(),
                "toCity": from_iata,
                "depart": _normalize_depart_date(end_date),
            }
            ret_api = await _fetch_flights_raw(request_id2, session_id2, return_body, cookies=auth_cookies2)
            ret_flights, _ = _extract_all_api_flights(ret_api)
            sorted_ret = _sort_flights(ret_flights, best_pref)
            if sorted_ret:
                return_flight_data = sorted_ret[0]
            applog.info(f"Itinerary: Found {len(ret_flights)} return flights")
        except Exception:
            applog.error(f"Itinerary flight search failed (non-fatal):\n{traceback.format_exc()}")

        # ── Gather hotel data (3-star default) ───────────────────────────
        hotel_query = custom_request if "hotel" in custom_request.lower() else "3 star"
        applog.info(f"Itinerary: Searching hotels in {destination} ({hotel_query})")
        hotels_list: List[Dict[str, Any]] = []
        try:
            hotels_list = await _search_hotels_with_places(destination, hotel_query, limit=3)
        except Exception:
            applog.error(f"Itinerary hotel search failed (non-fatal):\n{traceback.format_exc()}")

        # ── Gather sightseeing data ──────────────────────────────────────
        sightseeing_data: List[Dict[str, Any]] = []
        try:
            ss_prompt = f"""
You are a travel sightseeing assistant.
Provide top 6 must-do sightseeing recommendations for {destination}.
User preference: {custom_request if custom_request else "general"}

Requirements:
- Return valid JSON only.
- Include exactly 6 sightseeing spots, ordered by must-do first.
- For each item include: name, description, ideal_duration, best_time_to_visit
- Keep descriptions short.
"""
            ss_result = await _call_llm_json(ss_prompt, DestinationSightseeingResult)
            ss_data = ss_result.get("data") or {}
            sightseeing_data = ss_data.get("top_sightseeing") or []
        except Exception:
            applog.error(f"Itinerary sightseeing fetch failed (non-fatal):\n{traceback.format_exc()}")

        # ── Gather activities data ───────────────────────────────────────
        activities_data: List[Dict[str, Any]] = []
        try:
            act_prompt = f"""
You are a travel activities assistant.
Provide top 6 must-do activities for {destination}.
User preference: {custom_request if custom_request else "general"}

Requirements:
- Return valid JSON only.
- Include exactly 6 activities, ordered by must-do first.
- Each activity must include: name, description, category, ideal_duration
- category must be one of: adventure, nature, culture, family, relaxation, nightlife, shopping, food, water, wildlife, romantic, seasonal, general
"""
            act_result = await _call_llm_json(act_prompt, DestinationActivitiesResult)
            act_data = act_result.get("data") or {}
            activities_data = act_data.get("top_activities") or []
        except Exception:
            applog.error(f"Itinerary activities fetch failed (non-fatal):\n{traceback.format_exc()}")

        # ── Build consolidated LLM prompt for day-wise itinerary ─────────
        outbound_summary = ""
        if outbound_flight_data:
            outbound_summary = f"""Outbound Flight: {outbound_flight_data.get('airline_name', '')} {outbound_flight_data.get('flight_number', '')}, {outbound_flight_data.get('from_city', '')}→{outbound_flight_data.get('to_city', '')}, depart {outbound_flight_data.get('departure_time', '')}, arrive {outbound_flight_data.get('arrival_time', '')}, duration {outbound_flight_data.get('duration_text', '')}, price ₹{outbound_flight_data.get('price', '')}"""

        return_summary = ""
        if return_flight_data:
            return_summary = f"""Return Flight: {return_flight_data.get('airline_name', '')} {return_flight_data.get('flight_number', '')}, {return_flight_data.get('from_city', '')}→{return_flight_data.get('to_city', '')}, depart {return_flight_data.get('departure_time', '')}, arrive {return_flight_data.get('arrival_time', '')}, duration {return_flight_data.get('duration_text', '')}, price ₹{return_flight_data.get('price', '')}"""

        hotels_summary = json.dumps(hotels_list[:3], ensure_ascii=False) if hotels_list else "No hotel data available, suggest good 3-star options."
        sightseeing_summary = json.dumps(sightseeing_data[:6], ensure_ascii=False) if sightseeing_data else "Suggest top must-do sightseeing."
        activities_summary = json.dumps(activities_data[:6], ensure_ascii=False) if activities_data else "Suggest top must-do activities."

        itinerary_prompt = f"""
You are an expert travel itinerary planner. Create a detailed day-wise travel itinerary.

Trip Details:
- Destination: {destination}
- From: {from_city_raw}
- Start Date: {start_date}
- End Date: {end_date}
- Travellers: {adults} adults, {children} children, {infants} infants
- User Preferences: {custom_request if custom_request else "Standard trip with 3-star hotels, best flights, must-do activities and sightseeing"}

Available Flight Data:
{outbound_summary if outbound_summary else "No outbound flight data - suggest a reasonable flight."}
{return_summary if return_summary else "No return flight data - suggest a reasonable return flight."}

Available Hotels (use these for accommodation):
{hotels_summary}

Available Sightseeing (distribute across days, must-do first):
{sightseeing_summary}

Available Activities (distribute across days, must-do first):
{activities_summary}

IMPORTANT RULES:
1. Create one ItineraryDayPlan for each day of the trip.
2. Day 1: Include airport transfer (pickup) + outbound flight details + check-in to hotel + light sightseeing.
3. Last Day: Include checkout + return flight details + airport drop transfer.
4. Middle days: Full sightseeing + activities + local transfers between spots.
5. Every day MUST have a hotel (except last day if departure is late).
6. Include transfers between airport and hotel, and between major spots.
7. Include meal suggestions (breakfast, lunch, dinner) for each day.
8. Distribute sightseeing and activities evenly - put must-do items on earlier days.
9. Add practical notes for each day (best time to visit, what to carry, etc.)
10. Estimate total budget breakdown.
11. The title should be catchy like "3 Days in Magical Goa" or "Dubai Adventure: 5 Days".
12. For each sightseeing/activity item, set is_must_do=true for the top 2-3 items.

Return the full CustomItineraryResult JSON.
"""

        applog.info("Itinerary: Generating day-wise plan via LLM...")
        llm_result = await _call_llm_json(itinerary_prompt, CustomItineraryResult)
        itinerary = llm_result.get("data") or {}

        if not isinstance(itinerary, dict) or not itinerary:
            result = {
                "message": "Itinerary could not be generated.",
                "raw_text": llm_result.get("raw_text", ""),
                "code": 502,
            }
            _log_tool_output("create_custom_itinerary", result)
            return result

        # ── Inject real flight data into itinerary days ──────────────────
        days = itinerary.get("days") or []
        if days and outbound_flight_data:
            first_day = days[0]
            first_day["flight"] = {
                "airline_name": outbound_flight_data.get("airline_name", ""),
                "flight_number": outbound_flight_data.get("flight_number", ""),
                "from_city": outbound_flight_data.get("from_city", ""),
                "to_city": outbound_flight_data.get("to_city", ""),
                "departure_time": outbound_flight_data.get("departure_time", ""),
                "arrival_time": outbound_flight_data.get("arrival_time", ""),
                "duration_text": outbound_flight_data.get("duration_text", ""),
                "price": str(outbound_flight_data.get("price", "")),
                "currency": "INR",
                "stops_text": outbound_flight_data.get("stops_text", "Non-stop"),
                "airline_logo_url": outbound_flight_data.get("airline_logo_url", ""),
            }

        if days and return_flight_data:
            last_day = days[-1]
            last_day["flight"] = {
                "airline_name": return_flight_data.get("airline_name", ""),
                "flight_number": return_flight_data.get("flight_number", ""),
                "from_city": return_flight_data.get("from_city", ""),
                "to_city": return_flight_data.get("to_city", ""),
                "departure_time": return_flight_data.get("departure_time", ""),
                "arrival_time": return_flight_data.get("arrival_time", ""),
                "duration_text": return_flight_data.get("duration_text", ""),
                "price": str(return_flight_data.get("price", "")),
                "currency": "INR",
                "stops_text": return_flight_data.get("stops_text", "Non-stop"),
                "airline_logo_url": return_flight_data.get("airline_logo_url", ""),
            }

        # ── Inject real hotel data into days ─────────────────────────────
        if days and hotels_list:
            for i, day in enumerate(days):
                if day.get("hotel") and not day["hotel"].get("image_url"):
                    hotel_idx = i % len(hotels_list)
                    real_hotel = hotels_list[hotel_idx]
                    day["hotel"]["image_url"] = real_hotel.get("image_url", "")
                    if not day["hotel"].get("address"):
                        day["hotel"]["address"] = real_hotel.get("address", "")

        # ── Enrich images ────────────────────────────────────────────────
        applog.info("Itinerary: Enriching images via Places API...")
        try:
            itinerary["days"] = await _enrich_itinerary_images(destination, days)
        except Exception:
            applog.error(f"Itinerary image enrichment failed (non-fatal):\n{traceback.format_exc()}")

        # ── Hero image ───────────────────────────────────────────────────
        itinerary["hero_image_url"] = await _get_place_image_url(destination)
        itinerary["from_city"] = from_city_raw
        itinerary["trip_type"] = "domestic" if trip_type == "dom" else "international"

        result = {
            "itinerary_result": itinerary,
            "code": 200,
        }
        _log_tool_output("create_custom_itinerary", result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in create_custom_itinerary: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("create_custom_itinerary", result)
        return result


@function_tool()
@retry(wait=wait_random_exponential(multiplier=1, max=30), stop=stop_after_attempt(3))
async def update_custom_itinerary(
    context: RunContext,  # type: ignore
    existing_itinerary_json: Annotated[str, "The full existing itinerary as a JSON string (from the previous create_custom_itinerary or update_custom_itinerary result). Pass the complete itinerary_result object."],
    change_request: Annotated[str, "What the user wants to change (e.g. 'change Day 2 hotel to 5 star', 'add scuba diving on Day 3', 'remove the temple visit on Day 1')"],
) -> Dict[str, Any]:
    """
    Update an existing custom itinerary based on a user's change request.
    Only modifies the parts the user asked to change — keeps everything else intact.

    Use this when the user already has an itinerary and wants to modify specific parts
    (change hotel, swap activities, add/remove sightseeing, change flights, etc.)
    """
    payload = {
        "existing_itinerary_json": existing_itinerary_json[:200] + "...(truncated)",
        "change_request": change_request,
    }
    _log_tool_call("update_custom_itinerary", payload)

    try:
        change_request = _sanitize(change_request)
        if not change_request:
            result = {"message": "change_request is required.", "code": 400}
            _log_tool_output("update_custom_itinerary", result)
            return result

        # Parse existing itinerary
        existing: Dict[str, Any] = {}
        try:
            if isinstance(existing_itinerary_json, str):
                existing = json.loads(existing_itinerary_json)
            elif isinstance(existing_itinerary_json, dict):
                existing = existing_itinerary_json
        except Exception:
            pass

        if not existing:
            result = {"message": "Could not parse existing itinerary JSON.", "code": 400}
            _log_tool_output("update_custom_itinerary", result)
            return result

        destination = existing.get("destination", "")

        # ── Check if hotel change is requested → search for new hotels ───
        new_hotels_data = ""
        if any(kw in change_request.lower() for kw in ["hotel", "stay", "accommodation", "resort", "5 star", "4 star", "luxury"]):
            try:
                new_hotels = await _search_hotels_with_places(destination, change_request, limit=3)
                new_hotels_data = f"\nAvailable new hotels matching the request:\n{json.dumps(new_hotels, ensure_ascii=False)}"
            except Exception:
                applog.error(f"Update itinerary hotel search failed:\n{traceback.format_exc()}")

        # ── LLM prompt to update itinerary ───────────────────────────────
        update_prompt = f"""
You are an expert travel itinerary planner. You need to update an existing itinerary.

EXISTING ITINERARY (complete JSON):
{json.dumps(existing, ensure_ascii=False)}

USER'S CHANGE REQUEST:
"{change_request}"
{new_hotels_data}

CRITICAL RULES:
1. Return the COMPLETE updated itinerary in the same CustomItineraryResult JSON format.
2. ONLY modify what the user explicitly asked for. Keep ALL other days, hotels, flights, sightseeing, activities, transfers, and meals EXACTLY THE SAME.
3. If the user asks to change a hotel on Day 2, only change Day 2's hotel — do NOT touch Day 1, Day 3, etc.
4. If the user asks to add an activity, add it to the correct day and keep everything else intact.
5. If the user asks to remove something, remove only that specific item.
6. Preserve all existing image_url, airline_logo_url values unless the item itself is being replaced.
7. Update the title only if the nature of the trip significantly changes.
8. Recalculate total_estimated_budget if costs change.
9. Keep the same structure: title, destination, trip_type, from_city, start_date, end_date, adults, children, infants, days[], total_estimated_budget, budget_breakdown, tips, hero_image_url.
10. For any NEW sightseeing or activity items added, set image_url to empty string (will be enriched later).

Return the full updated itinerary JSON.
"""

        applog.info(f"Update itinerary: Processing change request: {change_request}")
        llm_result = await _call_llm_json(update_prompt, CustomItineraryResult)
        updated = llm_result.get("data") or {}

        if not isinstance(updated, dict) or not updated:
            result = {
                "message": "Updated itinerary could not be generated.",
                "raw_text": llm_result.get("raw_text", ""),
                "code": 502,
            }
            _log_tool_output("update_custom_itinerary", result)
            return result

        # ── Enrich any new images ────────────────────────────────────────
        days = updated.get("days") or []
        try:
            updated["days"] = await _enrich_itinerary_images(destination, days)
        except Exception:
            applog.error(f"Update itinerary image enrichment failed:\n{traceback.format_exc()}")

        # Preserve hero image if not changed
        if not updated.get("hero_image_url") and existing.get("hero_image_url"):
            updated["hero_image_url"] = existing["hero_image_url"]

        result = {
            "itinerary_result": updated,
            "code": 200,
        }
        _log_tool_output("update_custom_itinerary", result)
        return result

    except Exception as err:
        applog.error(f"Unexpected error in update_custom_itinerary: {err}\n{traceback.format_exc()}")
        result = {"message": "Unexpected error. Please try again.", "code": 500}
        _log_tool_output("update_custom_itinerary", result)
        return result

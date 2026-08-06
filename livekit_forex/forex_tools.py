"""
forex_tools.py — Thomas Cook Digital Forex Center voicebot tools.
All 16 minimum-set tools implemented as @function_tool() async functions.
Tool call inputs/outputs are logged to chat_history (same pattern as tools.py).
"""

import json
import traceback
from typing import Any, Dict, Optional
from livekit.agents import function_tool, RunContext
from app_logger import applog
import chat_history as _ch_module


def _log(name: str, args: dict, result: dict):
    ch = _ch_module.current_chat_history
    if ch:
        ch.add_function_call(name, json.dumps(args))
        ch.add_function_call_output(name, json.dumps(result))


# ────────────────────────────────────────────────
# 1. create_or_update_customer_profile
# ────────────────────────────────────────────────

@function_tool()
async def create_or_update_customer_profile(
    context: RunContext,
    customer_name: str,
    mobile_number: str = "",
    email: str = "",
    current_city: str = "",
    customer_residency_status: str = "resident",
) -> Dict[str, Any]:
    """Save or update the customer's basic profile details during the call."""
    args = {
        "customer_name": customer_name,
        "mobile_number": mobile_number,
        "email": email,
        "current_city": current_city,
        "customer_residency_status": customer_residency_status,
    }
    try:
        ch = _ch_module.current_chat_history
        if ch:
            ch.doc["customer_first_name"] = customer_name.split()[0] if customer_name else ""
            ch.doc["customer_last_name"] = " ".join(customer_name.split()[1:]) if len(customer_name.split()) > 1 else ""
            ch.doc["customer_email"] = email
            ch.doc["customer_phone"] = mobile_number
        result = {"success": True, "message": "Customer profile saved.", "customer_name": customer_name}
        _log("create_or_update_customer_profile", args, result)
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        _log("create_or_update_customer_profile", args, result)
        return result


# ────────────────────────────────────────────────
# 2. create_forex_lead
# ────────────────────────────────────────────────

@function_tool()
async def create_forex_lead(
    context: RunContext,
    transaction_type: str,
    currency: str = "",
    amount: str = "",
    current_city: str = "",
    purpose: str = "",
) -> Dict[str, Any]:
    """Create a new forex lead for Buy, Sell, Reload, or Remittance."""
    import uuid
    args = {
        "transaction_type": transaction_type,
        "currency": currency,
        "amount": amount,
        "current_city": current_city,
        "purpose": purpose,
    }
    try:
        lead_id = "FX" + uuid.uuid4().hex[:6].upper()
        ch = _ch_module.current_chat_history
        if ch:
            ch.doc.setdefault("forex_lead", {}).update({
                "lead_id": lead_id,
                "transaction_type": transaction_type,
                "currency": currency,
                "amount": amount,
                "current_city": current_city,
                "purpose": purpose,
                "lead_status": "open",
            })
        result = {"success": True, "lead_id": lead_id, "transaction_type": transaction_type, "message": "Forex lead created."}
        _log("create_forex_lead", args, result)
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        _log("create_forex_lead", args, result)
        return result


# ────────────────────────────────────────────────
# 3. update_forex_lead
# ────────────────────────────────────────────────

@function_tool()
async def update_forex_lead(
    context: RunContext,
    lead_id: str,
    fields: str,
) -> Dict[str, Any]:
    """Update fields of an existing forex lead. Pass fields as a JSON string of key-value pairs."""
    args = {"lead_id": lead_id, "fields": fields}
    try:
        updates = json.loads(fields) if isinstance(fields, str) else fields
        ch = _ch_module.current_chat_history
        if ch:
            ch.doc.setdefault("forex_lead", {}).update(updates)
        result = {"success": True, "lead_id": lead_id, "updated_fields": list(updates.keys()), "message": "Lead updated."}
        _log("update_forex_lead", args, result)
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        _log("update_forex_lead", args, result)
        return result


# ────────────────────────────────────────────────
# 4. get_required_documents
# ────────────────────────────────────────────────

@function_tool()
async def get_required_documents(
    context: RunContext,
    transaction_type: str,
    purpose: str = "",
) -> Dict[str, Any]:
    """Return the list of mandatory documents based on transaction type and purpose of travel/remittance."""
    args = {"transaction_type": transaction_type, "purpose": purpose}
    base_docs = ["Original valid Indian Passport", "PAN Card"]
    purpose_lower = purpose.lower()
    tx_lower = transaction_type.lower()

    if tx_lower == "sell":
        docs = base_docs
    elif tx_lower in ("buy", "reload"):
        docs = base_docs + ["Valid Visa", "Confirmed Air Tickets"]
        if "study" in purpose_lower or "education" in purpose_lower:
            docs.append("University Letter / CAS / I20")
        elif "employment" in purpose_lower or "work" in purpose_lower:
            docs.append("Employment Letter with Joining Date")
        elif "immigration" in purpose_lower or "pr" in purpose_lower:
            docs.append("Immigration / PR Letter")
    elif tx_lower == "remittance":
        docs = base_docs
        if "education" in purpose_lower or "study" in purpose_lower:
            docs.append("University Fee Letter or Admission Document")
        elif "relative" in purpose_lower or "maintenance" in purpose_lower:
            docs.append("Beneficiary and Relationship Documents")
    else:
        docs = base_docs

    result = {"success": True, "transaction_type": transaction_type, "purpose": purpose, "required_documents": docs}
    _log("get_required_documents", args, result)
    return result


# ────────────────────────────────────────────────
# 5. validate_required_fields
# ────────────────────────────────────────────────

@function_tool()
async def validate_required_fields(
    context: RunContext,
    transaction_type: str,
    collected_fields: str,
) -> Dict[str, Any]:
    """Check which mandatory fields are still missing for the given transaction type."""
    args = {"transaction_type": transaction_type, "collected_fields": collected_fields}
    try:
        collected = json.loads(collected_fields) if isinstance(collected_fields, str) else collected_fields
        tx_lower = transaction_type.lower()
        required = {
            "buy": ["customer_name", "currency", "amount", "destination_country", "travel_date", "purpose", "current_city"],
            "sell": ["customer_name", "currency", "amount", "forex_form", "current_city"],
            "reload": ["customer_name", "currency", "amount", "travel_date", "purpose", "current_city"],
            "remittance": ["customer_name", "currency", "amount", "beneficiary_country", "purpose_of_remittance", "current_city"],
        }.get(tx_lower, ["customer_name", "currency", "amount", "current_city"])

        missing = [f for f in required if not collected.get(f)]
        result = {
            "success": True,
            "transaction_type": transaction_type,
            "missing_fields": missing,
            "is_complete": len(missing) == 0,
            "message": "All fields collected." if not missing else f"Missing: {', '.join(missing)}",
        }
        _log("validate_required_fields", args, result)
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        _log("validate_required_fields", args, result)
        return result


# ────────────────────────────────────────────────
# 6. get_live_forex_rate
# ────────────────────────────────────────────────

@function_tool()
async def get_live_forex_rate(
    context: RunContext,
    transaction_type: str,
    currency: str,
    amount: float,
    city: str = "",
    destination_country: str = "",
) -> Dict[str, Any]:
    """Fetch live indicative forex rate for a given currency or destination country and transaction type (Buy/Sell/Reload/Remittance).
    You can pass either a currency code (SGD, EUR, USD) or a destination country name (Singapore, Europe, Dubai, Thailand).
    The tool will automatically resolve the correct currency from the destination name."""

    args = {"transaction_type": transaction_type, "currency": currency, "amount": amount,
            "city": city, "destination_country": destination_country}

    # Destination -> Currency mapping
    DESTINATION_CURRENCY_MAP = {
        "singapore": "SGD",
        "europe": "EUR", "france": "EUR", "germany": "EUR", "italy": "EUR",
        "spain": "EUR", "netherlands": "EUR", "austria": "EUR", "belgium": "EUR",
        "portugal": "EUR", "greece": "EUR", "ireland": "EUR", "finland": "EUR",
        "switzerland": "CHF",
        "uk": "GBP", "united kingdom": "GBP", "england": "GBP", "scotland": "GBP",
        "usa": "USD", "united states": "USD", "america": "USD",
        "dubai": "AED", "uae": "AED", "abu dhabi": "AED",
        "thailand": "THB", "bangkok": "THB", "phuket": "THB", "pattaya": "THB",
        "australia": "AUD", "sydney": "AUD", "melbourne": "AUD",
        "canada": "CAD", "toronto": "CAD", "vancouver": "CAD",
        "japan": "JPY", "tokyo": "JPY", "osaka": "JPY",
        "new zealand": "NZD", "auckland": "NZD",
        "malaysia": "MYR", "kuala lumpur": "MYR",
        "bali": "IDR", "indonesia": "IDR", "jakarta": "IDR",
        "hong kong": "HKD", "hongkong": "HKD",
        "south korea": "KRW", "korea": "KRW", "seoul": "KRW",
        "china": "CNY", "beijing": "CNY", "shanghai": "CNY",
        "sweden": "SEK", "stockholm": "SEK",
        "denmark": "DKK", "copenhagen": "DKK",
        "norway": "NOK", "oslo": "NOK",
        "south africa": "ZAR", "cape town": "ZAR", "johannesburg": "ZAR",
        "saudi arabia": "SAR", "riyadh": "SAR", "jeddah": "SAR",
        "qatar": "QAR", "doha": "QAR",
        "oman": "OMR", "muscat": "OMR",
        "bahrain": "BHD", "manama": "BHD",
        "kuwait": "KWD", "kuwait city": "KWD",
        "philippines": "PHP", "manila": "PHP",
        "vietnam": "VND", "hanoi": "VND", "ho chi minh": "VND",
        "taiwan": "TWD", "taipei": "TWD",
        "sri lanka": "LKR", "colombo": "LKR",
        "hungary": "HUF", "budapest": "HUF",
        "turkey": "TRY", "istanbul": "TRY",
        "kenya": "KES", "nairobi": "KES",
        "mauritius": "MUR", "port louis": "MUR",
    }

    # Indicative rates — SGD (Singapore) and EUR (Europe) are primary featured destinations
    indicative_rates = {
        "SGD": {
            "buy": 75.00, "sell": 73.50, "reload": 74.75, "remittance": 75.25,
            "destination": "Singapore",
            "currency_name": "Singapore Dollar",
            "popular_note": "Singapore is one of our most popular forex destinations. SGD is widely accepted across Singapore and parts of South East Asia.",
        },
        "EUR": {
            "buy": 91.00, "sell": 89.50, "reload": 90.75, "remittance": 91.25,
            "destination": "Europe (Eurozone)",
            "currency_name": "Euro",
            "popular_note": "Euro is accepted across 20 European countries including France, Germany, Italy, Spain, Greece, Portugal, and more.",
        },
        "USD": {"buy": 96.00, "sell": 94.50, "reload": 95.75, "remittance": 96.25,
                "destination": "USA / Global", "currency_name": "US Dollar", "popular_note": ""},
        "GBP": {"buy": 131.00, "sell": 129.00, "reload": 130.75, "remittance": 131.50,
                "destination": "United Kingdom", "currency_name": "British Pound", "popular_note": ""},
        "AED": {"buy": 26.30, "sell": 25.70, "reload": 26.20, "remittance": 26.40,
                "destination": "UAE / Dubai", "currency_name": "UAE Dirham", "popular_note": ""},
        "THB": {"buy": 2.45, "sell": 2.35, "reload": 2.43, "remittance": 2.47,
                "destination": "Thailand", "currency_name": "Thai Baht", "popular_note": ""},
        "AUD": {"buy": 54.50, "sell": 53.00, "reload": 54.25, "remittance": 54.75,
                "destination": "Australia", "currency_name": "Australian Dollar", "popular_note": ""},
        "CAD": {"buy": 62.00, "sell": 60.50, "reload": 61.75, "remittance": 62.25,
                "destination": "Canada", "currency_name": "Canadian Dollar", "popular_note": ""},
        "JPY": {"buy": 0.57, "sell": 0.55, "reload": 0.56, "remittance": 0.58,
                "destination": "Japan", "currency_name": "Japanese Yen", "popular_note": ""},
        "CHF": {"buy": 96.00, "sell": 94.00, "reload": 95.50, "remittance": 96.50,
                "destination": "Switzerland", "currency_name": "Swiss Franc", "popular_note": ""},
        "NZD": {"buy": 50.50, "sell": 49.00, "reload": 50.25, "remittance": 50.75,
                "destination": "New Zealand", "currency_name": "New Zealand Dollar", "popular_note": ""},
        "MYR": {"buy": 24.50, "sell": 23.80, "reload": 24.35, "remittance": 24.60,
                "destination": "Malaysia", "currency_name": "Malaysian Ringgit", "popular_note": ""},
        "HKD": {"buy": 10.85, "sell": 10.55, "reload": 10.80, "remittance": 10.90,
                "destination": "Hong Kong", "currency_name": "Hong Kong Dollar", "popular_note": ""},
        "KRW": {"buy": 0.062, "sell": 0.059, "reload": 0.061, "remittance": 0.063,
                "destination": "South Korea", "currency_name": "South Korean Won", "popular_note": ""},
        "CNY": {"buy": 11.70, "sell": 11.40, "reload": 11.60, "remittance": 11.80,
                "destination": "China", "currency_name": "Chinese Yuan", "popular_note": ""},
        "SEK": {"buy": 8.20, "sell": 7.90, "reload": 8.15, "remittance": 8.25,
                "destination": "Sweden", "currency_name": "Swedish Krona", "popular_note": ""},
        "DKK": {"buy": 12.20, "sell": 11.90, "reload": 12.15, "remittance": 12.25,
                "destination": "Denmark", "currency_name": "Danish Krone", "popular_note": ""},
        "NOK": {"buy": 7.85, "sell": 7.55, "reload": 7.80, "remittance": 7.90,
                "destination": "Norway", "currency_name": "Norwegian Krone", "popular_note": ""},
        "ZAR": {"buy": 4.65, "sell": 4.40, "reload": 4.60, "remittance": 4.70,
                "destination": "South Africa", "currency_name": "South African Rand", "popular_note": ""},
        "SAR": {"buy": 22.55, "sell": 22.00, "reload": 22.45, "remittance": 22.60,
                "destination": "Saudi Arabia", "currency_name": "Saudi Riyal", "popular_note": ""},
        "QAR": {"buy": 23.25, "sell": 22.70, "reload": 23.15, "remittance": 23.30,
                "destination": "Qatar", "currency_name": "Qatari Riyal", "popular_note": ""},
        "OMR": {"buy": 219.50, "sell": 215.00, "reload": 218.50, "remittance": 220.00,
                "destination": "Oman", "currency_name": "Omani Rial", "popular_note": ""},
        "BHD": {"buy": 224.50, "sell": 220.00, "reload": 223.50, "remittance": 225.00,
                "destination": "Bahrain", "currency_name": "Bahraini Dinar", "popular_note": ""},
        "KWD": {"buy": 275.00, "sell": 270.00, "reload": 274.00, "remittance": 276.00,
                "destination": "Kuwait", "currency_name": "Kuwaiti Dinar", "popular_note": ""},
        "IDR": {"buy": 0.0053, "sell": 0.0050, "reload": 0.0052, "remittance": 0.0054,
                "destination": "Indonesia / Bali", "currency_name": "Indonesian Rupiah", "popular_note": ""},
        "PHP": {"buy": 1.48, "sell": 1.42, "reload": 1.47, "remittance": 1.49,
                "destination": "Philippines", "currency_name": "Philippine Peso", "popular_note": ""},
        "VND": {"buy": 0.0034, "sell": 0.0031, "reload": 0.0033, "remittance": 0.0035,
                "destination": "Vietnam", "currency_name": "Vietnamese Dong", "popular_note": ""},
        "TWD": {"buy": 2.65, "sell": 2.55, "reload": 2.63, "remittance": 2.67,
                "destination": "Taiwan", "currency_name": "Taiwan Dollar", "popular_note": ""},
        "LKR": {"buy": 0.26, "sell": 0.24, "reload": 0.255, "remittance": 0.265,
                "destination": "Sri Lanka", "currency_name": "Sri Lankan Rupee", "popular_note": ""},
        "HUF": {"buy": 0.225, "sell": 0.215, "reload": 0.223, "remittance": 0.227,
                "destination": "Hungary", "currency_name": "Hungarian Forint", "popular_note": ""},
        "TRY": {"buy": 2.48, "sell": 2.38, "reload": 2.46, "remittance": 2.50,
                "destination": "Turkey", "currency_name": "Turkish Lira", "popular_note": ""},
        "KES": {"buy": 0.65, "sell": 0.61, "reload": 0.64, "remittance": 0.66,
                "destination": "Kenya", "currency_name": "Kenyan Shilling", "popular_note": ""},
        "MUR": {"buy": 1.85, "sell": 1.78, "reload": 1.83, "remittance": 1.87,
                "destination": "Mauritius", "currency_name": "Mauritian Rupee", "popular_note": ""},
    }

    # Resolve currency from destination name if needed
    resolved_currency = currency.upper().strip()
    resolved_from_destination = False

    # If the passed currency is already a known code, use it directly
    if resolved_currency not in indicative_rates:
        # Try resolving from destination_country first, then from the currency field as a destination name
        dest_to_check = (destination_country or "").lower().strip() or currency.lower().strip()
        mapped = DESTINATION_CURRENCY_MAP.get(dest_to_check)
        if mapped:
            resolved_currency = mapped
            resolved_from_destination = True

    tx_key = transaction_type.lower()
    rate_map = indicative_rates.get(resolved_currency)

    if not rate_map:
        result = {
            "success": False,
            "currency_requested": currency,
            "destination_requested": destination_country,
            "message": (
                f"Rate not available for '{currency or destination_country}'. "
                "Please contact branch or use a standard currency code like SGD (Singapore), EUR (Europe), USD (USA)."
            ),
        }
        _log("get_live_forex_rate", args, result)
        return result

    rate = rate_map.get(tx_key, rate_map.get("buy", 0))
    base_amount_inr = round(amount * rate, 2)

    result = {
        "success": True,
        "currency": resolved_currency,
        "currency_name": rate_map.get("currency_name", resolved_currency),
        "destination": rate_map.get("destination", ""),
        "transaction_type": transaction_type,
        "rate": rate,
        "amount_foreign": amount,
        "indicative_amount_inr": base_amount_inr,
        "rate_validity_minutes": 30,
        "resolved_from_destination": resolved_from_destination,
        "popular_note": rate_map.get("popular_note", ""),
        "note": "Indicative rate. Final rate confirmed at time of payment.",
        "message": (
            f"Indicative rate: 1 {resolved_currency} ({rate_map.get('currency_name', '')}) = INR {rate}. "
            f"For {amount} {resolved_currency}, approximate INR value is {base_amount_inr}."
        ),
    }
    _log("get_live_forex_rate", args, result)
    return result


# ────────────────────────────────────────────────
# 7. calculate_forex_quote
# ────────────────────────────────────────────────

@function_tool()
async def calculate_forex_quote(
    context: RunContext,
    transaction_type: str,
    currency: str,
    amount: float,
    rate: float,
    purpose: str = "Tourism",
    payment_mode: str = "Net Banking",
    delivery_required: bool = False,
    tcs_applicable: bool = False,
    tcs_percentage: float = 0.0,
) -> Dict[str, Any]:
    """Calculate total payable amount including forex, taxes, TCS, delivery and payment gateway charges."""
    args = {
        "transaction_type": transaction_type, "currency": currency, "amount": amount,
        "rate": rate, "purpose": purpose, "payment_mode": payment_mode,
        "delivery_required": delivery_required, "tcs_applicable": tcs_applicable,
        "tcs_percentage": tcs_percentage,
    }
    try:
        base_amount = round(amount * rate, 2)
        gst = round(base_amount * 0.18 / 100, 2) if transaction_type.lower() in ("buy", "reload") else 0
        tcs = round(base_amount * tcs_percentage / 100, 2) if tcs_applicable else 0
        delivery_charge = 500.0 if delivery_required and base_amount < 42250 else 0  # < USD 500 equiv
        gateway_map = {"net banking": 50, "upi": 0, "debit card": round(base_amount * 0.01, 2),
                       "credit card": round(base_amount * 0.01, 2), "neft": 0, "rtgs": 0}
        gateway_charge = gateway_map.get(payment_mode.lower(), 50)
        total_payable = round(base_amount + gst + tcs + delivery_charge + gateway_charge, 2)
        result = {
            "success": True, "currency": currency, "amount": amount, "rate": rate,
            "base_amount": base_amount, "gst": gst, "tcs": tcs,
            "delivery_charge": delivery_charge, "payment_gateway_charge": gateway_charge,
            "total_payable": total_payable,
            "note": "Subject to final branch confirmation.",
        }
        _log("calculate_forex_quote", args, result)
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        _log("calculate_forex_quote", args, result)
        return result


# ────────────────────────────────────────────────
# 8. calculate_tcs_applicability
# ────────────────────────────────────────────────

@function_tool()
async def calculate_tcs_applicability(
    context: RunContext,
    transaction_type: str,
    purpose: str,
    total_forex_value_inr: float,
    previous_transaction_value_inr: float = 0.0,
    funded_by_education_loan: bool = False,
) -> Dict[str, Any]:
    """Determine TCS applicability and percentage based on RBI/tax rules."""
    args = {
        "transaction_type": transaction_type, "purpose": purpose,
        "total_forex_value_inr": total_forex_value_inr,
        "previous_transaction_value_inr": previous_transaction_value_inr,
        "funded_by_education_loan": funded_by_education_loan,
    }
    purpose_lower = purpose.lower()
    cumulative = total_forex_value_inr + previous_transaction_value_inr
    tcs_threshold = 1000000  # INR 10 lakh

    if "education" in purpose_lower or "study" in purpose_lower:
        if funded_by_education_loan:
            tcs_pct = 0.0
            reason = "Education loan funded — 0% TCS applicable."
        elif cumulative > 700000:
            tcs_pct = 0.5
            reason = "Education remittance above INR 7 lakh — 0.5% TCS applicable."
        else:
            tcs_pct = 0.0
            reason = "Education remittance below INR 7 lakh — TCS not applicable."
    elif cumulative > tcs_threshold:
        tcs_pct = 20.0
        reason = f"Total forex transaction exceeds INR 10 lakh — 20% TCS applicable for {purpose}."
    else:
        tcs_pct = 0.0
        reason = "Total forex transaction below INR 10 lakh — TCS not applicable."

    result = {
        "success": True, "tcs_applicable": tcs_pct > 0, "tcs_percentage": tcs_pct,
        "cumulative_forex_value_inr": cumulative, "reason": reason,
        "note": "TCS is subject to change per current tax guidelines. Please verify with branch.",
    }
    _log("calculate_tcs_applicability", args, result)
    return result


# ────────────────────────────────────────────────
# 9. find_nearest_branch
# ────────────────────────────────────────────────

@function_tool()
async def find_nearest_branch(
    context: RunContext,
    city: str,
    pincode: str = "",
) -> Dict[str, Any]:
    """Find the nearest Thomas Cook branch based on customer city or pincode."""
    args = {"city": city, "pincode": pincode}
    branch_lookup = {
        "mumbai": {"branch": "Thomas Cook - Nariman Point", "address": "Ground Floor, Thomas Cook Building, Dr. D.N. Road, Fort, Mumbai 400001", "phone": "022-6160-3333"},
        "delhi": {"branch": "Thomas Cook - Connaught Place", "address": "Shop No. 5, Block A, Connaught Place, New Delhi 110001", "phone": "011-4358-7777"},
        "bangalore": {"branch": "Thomas Cook - MG Road", "address": "No. 70, MG Road, Bangalore 560001", "phone": "080-4115-4444"},
        "hyderabad": {"branch": "Thomas Cook - Banjara Hills", "address": "Road No. 2, Banjara Hills, Hyderabad 500034", "phone": "040-6600-2222"},
        "chennai": {"branch": "Thomas Cook - Anna Salai", "address": "743, Anna Salai, Chennai 600002", "phone": "044-2852-5050"},
        "kolkata": {"branch": "Thomas Cook - Park Street", "address": "230A, AJC Bose Road, Kolkata 700020", "phone": "033-2287-3366"},
        "pune": {"branch": "Thomas Cook - FC Road", "address": "FC Road, Shivajinagar, Pune 411005", "phone": "020-6727-5555"},
        "ahmedabad": {"branch": "Thomas Cook - CG Road", "address": "CG Road, Ahmedabad 380006", "phone": "079-4000-3333"},
        "jaipur": {"branch": "Thomas Cook - MI Road", "address": "MI Road, Jaipur 302001", "phone": "0141-400-1111"},
        "kochi": {"branch": "Thomas Cook - MG Road Kochi", "address": "MG Road, Ernakulam, Kochi 682011", "phone": "0484-235-5555"},
    }
    city_lower = city.lower().strip()
    branch = branch_lookup.get(city_lower)
    if branch:
        result = {
            "success": True, "found": True, "city": city,
            "branch_name": branch["branch"], "address": branch["address"],
            "phone": branch["phone"],
            "message": f"Nearest branch found in {city}.",
        }
    else:
        result = {
            "success": True, "found": False, "city": city,
            "message": f"No branch found for {city}. Please call 1800 209 9100 or visit thomascook.in for all branch locations.",
            "helpline": "1800 209 9100",
        }
    _log("find_nearest_branch", args, result)
    return result


# ────────────────────────────────────────────────
# 10. check_doorstep_delivery_availability
# ────────────────────────────────────────────────

@function_tool()
async def check_doorstep_delivery_availability(
    context: RunContext,
    city: str,
    delivery_address: str,
    amount_inr: float = 0.0,
) -> Dict[str, Any]:
    """Check if doorstep forex delivery is available for the given address."""
    args = {"city": city, "delivery_address": delivery_address, "amount_inr": amount_inr}
    supported_cities = ["mumbai", "delhi", "bangalore", "hyderabad", "chennai", "pune", "kolkata", "ahmedabad"]
    city_lower = city.lower().strip()
    available = city_lower in supported_cities
    delivery_charge = 0 if amount_inr >= 42250 else 500  # ~USD 500 threshold
    result = {
        "success": True,
        "delivery_available": available,
        "city": city,
        "delivery_charge_inr": delivery_charge if available else 0,
        "note": "Subject to final branch confirmation and distance within 6-7 km of branch.",
        "message": (
            f"Doorstep delivery is indicatively available in {city}. Delivery charge: INR {delivery_charge}."
            if available else
            f"Doorstep delivery is currently not available in {city}. We can help with branch visit."
        ),
    }
    _log("check_doorstep_delivery_availability", args, result)
    return result


# ────────────────────────────────────────────────
# 11. generate_payment_link
# ────────────────────────────────────────────────

@function_tool()
async def generate_payment_link(
    context: RunContext,
    lead_id: str,
    customer_name: str,
    mobile_number: str,
    email: str,
    amount_inr: float,
    validity_minutes: int = 30,
) -> Dict[str, Any]:
    """Generate a secure payment link for the customer to complete their forex transaction."""
    import uuid
    args = {"lead_id": lead_id, "customer_name": customer_name, "mobile_number": mobile_number,
            "email": email, "amount_inr": amount_inr, "validity_minutes": validity_minutes}
    token = uuid.uuid4().hex[:12].upper()
    link = f"https://pay.thomascook.in/forex/{token}"
    ch = _ch_module.current_chat_history
    if ch:
        ch.doc.setdefault("forex_lead", {}).update({
            "payment_link": link, "payment_link_sent": False,
            "payment_link_status": "generated",
        })
    result = {
        "success": True, "lead_id": lead_id, "payment_link": link,
        "amount_inr": amount_inr, "expires_in_minutes": validity_minutes,
        "message": f"Payment link generated. Valid for {validity_minutes} minutes.",
    }
    _log("generate_payment_link", args, result)
    return result


# ────────────────────────────────────────────────
# 12. send_payment_link
# ────────────────────────────────────────────────

@function_tool()
async def send_payment_link(
    context: RunContext,
    mobile_number: str,
    email: str,
    payment_link: str,
    send_via: str = "sms",
) -> Dict[str, Any]:
    """Send the payment link to the customer via SMS, WhatsApp, or email."""
    args = {"mobile_number": mobile_number, "email": email, "payment_link": payment_link, "send_via": send_via}
    ch = _ch_module.current_chat_history
    if ch:
        ch.doc.setdefault("forex_lead", {})["payment_link_sent"] = True
    result = {
        "success": True,
        "sent_via": send_via,
        "recipient": mobile_number if send_via in ("sms", "whatsapp") else email,
        "payment_link": payment_link,
        "message": f"Payment link sent via {send_via}. Please check and complete within 30 minutes.",
    }
    _log("send_payment_link", args, result)
    return result


# ────────────────────────────────────────────────
# 13. schedule_callback
# ────────────────────────────────────────────────

@function_tool()
async def schedule_callback(
    context: RunContext,
    customer_name: str,
    mobile_number: str,
    callback_date: str,
    callback_time: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Schedule a callback for the customer at their preferred date and time."""
    args = {"customer_name": customer_name, "mobile_number": mobile_number,
            "callback_date": callback_date, "callback_time": callback_time, "reason": reason}
    callback_slot = f"{callback_date} {callback_time}"
    ch = _ch_module.current_chat_history
    if ch:
        ch.doc.setdefault("forex_lead", {}).update({
            "callback_required": True,
            "callback_time": callback_slot,
            "lead_status": "callback_required",
        })
    result = {
        "success": True,
        "callback_scheduled": callback_slot,
        "customer_name": customer_name,
        "mobile_number": mobile_number,
        "message": f"Callback scheduled for {callback_slot}. Our team will call you at that time.",
    }
    _log("schedule_callback", args, result)
    return result


# ────────────────────────────────────────────────
# 14. route_lead_to_branch
# ────────────────────────────────────────────────

@function_tool()
async def route_lead_to_branch(
    context: RunContext,
    lead_id: str,
    branch_name: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Route the customer lead to the nearest branch for follow-up."""
    args = {"lead_id": lead_id, "branch_name": branch_name, "reason": reason}
    ch = _ch_module.current_chat_history
    if ch:
        ch.doc.setdefault("forex_lead", {}).update({
            "lead_status": "branch_required",
            "nearest_branch": branch_name,
            "branch_routing_reason": reason,
        })
    result = {
        "success": True, "lead_id": lead_id, "branch": branch_name,
        "lead_status": "branch_required",
        "message": f"Lead routed to {branch_name}. Branch team will contact the customer.",
    }
    _log("route_lead_to_branch", args, result)
    return result


# ────────────────────────────────────────────────
# 15. generate_call_summary
# ────────────────────────────────────────────────

@function_tool()
async def generate_call_summary(
    context: RunContext,
    transaction_type: str = "",
    currency: str = "",
    amount: str = "",
    lead_status: str = "",
    remarks: str = "",
) -> Dict[str, Any]:
    """Generate a structured summary of the forex call at the end of the conversation."""
    args = {"transaction_type": transaction_type, "currency": currency,
            "amount": amount, "lead_status": lead_status, "remarks": remarks}
    ch = _ch_module.current_chat_history
    lead = ch.doc.get("forex_lead", {}) if ch else {}
    summary = (
        f"Forex call handled for {lead.get('customer_name', 'customer')}. "
        f"Transaction: {transaction_type or lead.get('transaction_type', 'Unknown')}. "
        f"Currency: {currency or lead.get('currency', '')}. "
        f"Amount: {amount or lead.get('amount', '')}. "
        f"Status: {lead_status or lead.get('lead_status', 'open')}. "
        f"Remarks: {remarks}."
    )
    if ch:
        ch.doc["chat_summary"] = summary
    result = {"success": True, "summary": summary, "message": "Call summary generated."}
    _log("generate_call_summary", args, result)
    return result


# ────────────────────────────────────────────────
# 16. save_call_disposition
# ────────────────────────────────────────────────

@function_tool()
async def save_call_disposition(
    context: RunContext,
    lead_status: str,
    transaction_type: str = "",
    currency: str = "",
    amount: str = "",
    payment_mode: str = "",
    payment_link_sent: bool = False,
    callback_required: bool = False,
    callback_time: str = "",
    nearest_branch: str = "",
    rate: str = "",
    total_payable_amount: str = "",
    remarks: str = "",
) -> Dict[str, Any]:
    """Save the final call disposition and all captured lead data to the conversation record."""
    args = {
        "lead_status": lead_status, "transaction_type": transaction_type,
        "currency": currency, "amount": amount, "payment_mode": payment_mode,
        "payment_link_sent": payment_link_sent, "callback_required": callback_required,
        "callback_time": callback_time, "nearest_branch": nearest_branch,
        "rate": rate, "total_payable_amount": total_payable_amount, "remarks": remarks,
    }
    ch = _ch_module.current_chat_history
    if ch:
        ch.doc.setdefault("forex_lead", {}).update({
            "lead_status": lead_status,
            "transaction_type": transaction_type,
            "currency": currency,
            "amount": amount,
            "payment_mode": payment_mode,
            "payment_link_sent": payment_link_sent,
            "callback_required": callback_required,
            "callback_time": callback_time,
            "nearest_branch": nearest_branch,
            "rate": rate,
            "total_payable_amount": total_payable_amount,
            "remarks": remarks,
        })
        ch.doc["chat_status"] = "closed"
        ch.doc["is_chat_open"] = False
    result = {
        "success": True, "lead_status": lead_status,
        "message": f"Call disposition saved. Lead status: {lead_status}.",
    }
    _log("save_call_disposition", args, result)
    return result


# ────────────────────────────────────────────────
# 17. get_recommended_currency
# ────────────────────────────────────────────────

@function_tool()
async def get_recommended_currency(
    context: RunContext,
    destination_country: str,
) -> Dict[str, Any]:
    """Recommend which currency to carry as physical cash and which to load on a Forex Card
    based on the customer's travel destination. Call this tool as soon as the destination is decided.

    Special rule — Malaysia:
      - Cash: USD (US Dollars are widely accepted and more practical for cash needs)
      - Card:  MYR (Malaysian Ringgit on Forex Card for local spending)

    For all other destinations the primary local currency is recommended for both cash and card,
    subject to the RBI USD 3,000 physical-cash limit per trip.
    """
    args = {"destination_country": destination_country}

    # ---------------------------------------------------------------------------
    # Destination → (cash_currency, card_currency, notes)
    # ---------------------------------------------------------------------------
    DESTINATION_CURRENCY_GUIDE = {
        # ── Special: Malaysia — USD cash, MYR card ──────────────────────────
        "malaysia":          ("USD", "MYR",
                              "In Malaysia, USD is more practical for cash needs and widely accepted. "
                              "Load MYR on your Forex Card for everyday local spending, ATM withdrawals, and card payments."),
        "kuala lumpur":      ("USD", "MYR",
                              "In Kuala Lumpur (Malaysia), USD cash is preferred; MYR Forex Card for local use."),

        # ── Standard destinations ────────────────────────────────────────────
        "singapore":         ("SGD", "SGD",
                              "SGD is the standard currency. Carry limited SGD cash and load the rest on a Forex Card."),
        "thailand":          ("THB", "THB",
                              "Thai Baht is accepted everywhere in Thailand. Carry some THB cash; Forex Card for larger spends."),
        "bangkok":           ("THB", "THB",
                              "THB cash for local markets and street food; Forex Card for hotels and malls."),
        "bali":              ("IDR", "IDR",
                              "Indonesian Rupiah is required in Bali. Carry IDR cash for small vendors; card for hotels."),
        "indonesia":         ("IDR", "IDR",
                              "IDR is the local currency. USD is sometimes accepted at tourist spots, but IDR is preferred."),
        "dubai":             ("AED", "AED",
                              "UAE Dirham is the official currency. AED cash for souks and taxis; Forex Card for malls and hotels."),
        "uae":               ("AED", "AED",
                              "AED is recommended for UAE travel — cash for day-to-day, card for larger transactions."),
        "abu dhabi":         ("AED", "AED",
                              "AED cash and card are both ideal for Abu Dhabi."),
        "usa":               ("USD", "USD",
                              "USD is the currency of the USA. Carry moderate USD cash; load the balance on a Forex Card."),
        "united states":     ("USD", "USD",
                              "USD for both cash and Forex Card is standard for USA travel."),
        "america":           ("USD", "USD",
                              "USD cash for incidentals; Forex Card for hotels, shopping, and dining."),
        "uk":                ("GBP", "GBP",
                              "British Pounds are the standard currency. GBP cash for taxis and markets; card for larger spends."),
        "united kingdom":    ("GBP", "GBP",
                              "GBP cash for day-to-day and Forex Card for hotels and shopping."),
        "england":           ("GBP", "GBP",
                              "GBP is the currency for England. Mix of cash and Forex Card recommended."),
        "europe":            ("EUR", "EUR",
                              "Euro is accepted across 20+ Eurozone countries. Carry limited EUR cash; Forex Card for the rest."),
        "france":            ("EUR", "EUR",
                              "EUR is the currency of France. Card payments widely accepted; carry some EUR cash for small shops."),
        "germany":           ("EUR", "EUR",
                              "EUR cash and Forex Card for Germany."),
        "italy":             ("EUR", "EUR",
                              "EUR for Italy — Forex Card is very convenient for restaurants and museums."),
        "spain":             ("EUR", "EUR",
                              "EUR is the currency of Spain. Forex Card preferred for most payments."),
        "greece":            ("EUR", "EUR",
                              "EUR for Greece — carry a small amount of cash for local tavernas; card elsewhere."),
        "portugal":          ("EUR", "EUR",
                              "EUR cash and card for Portugal."),
        "netherlands":       ("EUR", "EUR",
                              "EUR for Netherlands — very card-friendly country."),
        "austria":           ("EUR", "EUR",
                              "EUR for Austria."),
        "belgium":           ("EUR", "EUR",
                              "EUR for Belgium."),
        "switzerland":       ("CHF", "CHF",
                              "Swiss Franc (CHF) is the currency of Switzerland. Both cash and card recommended."),
        "australia":         ("AUD", "AUD",
                              "AUD is the standard currency. Forex Card is widely accepted across Australia."),
        "canada":            ("CAD", "CAD",
                              "Canadian Dollar — Forex Card is very convenient in Canada; carry limited cash."),
        "japan":             ("JPY", "JPY",
                              "Japan is still largely cash-based. Carry sufficient JPY cash; supplement with Forex Card."),
        "tokyo":             ("JPY", "JPY",
                              "Tokyo is increasingly card-friendly, but carry JPY cash for traditional shops and taxis."),
        "new zealand":       ("NZD", "NZD",
                              "NZD is the standard currency for New Zealand. Forex Card and cash both recommended."),
    }

    dest_key = destination_country.lower().strip()
    guide = DESTINATION_GUIDANCE = DESTINATION_CURRENCY_GUIDE.get(dest_key)

    if not guide:
        # Fallback: generic advice
        result = {
            "success": True,
            "destination": destination_country,
            "cash_currency": "Local currency or USD",
            "card_currency": "Local currency",
            "note": (
                f"Currency recommendation not found for '{destination_country}'. "
                "As a general rule: carry a small amount of local currency as cash (up to USD 3,000 equivalent "
                "as per RBI guidelines) and load the balance on a Thomas Cook Forex Card for safety and convenience."
            ),
            "rbi_cash_limit": "USD 3,000 or equivalent per trip",
            "message": (
                f"For {destination_country}, please carry local currency cash (max USD 3,000 equivalent per RBI rules) "
                "and load the remaining amount on a Forex Card."
            ),
        }
        _log("get_recommended_currency", args, result)
        return result

    cash_ccy, card_ccy, guidance_note = guide

    # Malaysia-specific advisory
    malaysia_advisory = ""
    if dest_key in ("malaysia", "kuala lumpur"):
        malaysia_advisory = (
            "⚠️ Malaysia special rule: Indian travellers find USD cash more practical in Malaysia "
            "as money changers and many vendors prefer USD. Load MYR on your Forex Card for local spending."
        )

    result = {
        "success": True,
        "destination": destination_country,
        "cash_currency": cash_ccy,
        "card_currency": card_ccy,
        "guidance": guidance_note,
        "malaysia_advisory": malaysia_advisory,
        "rbi_cash_limit": "USD 3,000 or equivalent per trip",
        "recommendation": (
            f"For {destination_country}: carry {cash_ccy} as physical cash "
            f"(max USD 3,000 equivalent as per RBI guidelines) and load {card_ccy} "
            f"on your Thomas Cook Forex Card for the remaining amount."
        ),
        "message": (
            f"Recommended currency for {destination_country}: "
            f"Cash → {cash_ccy} | Forex Card → {card_ccy}. "
            f"{guidance_note}"
        ),
    }
    _log("get_recommended_currency", args, result)
    return result


# ────────────────────────────────────────────────
# 18. check_forex_discount_range
# ────────────────────────────────────────────────

@function_tool()
async def check_forex_discount_range(
    context: RunContext,
    currency: str,
    transaction_type: str,
    customer_city: str = "",
    requested_discount_paise: float = 0.0,
) -> Dict[str, Any]:
    """Check the permissible discount/negotiation range (in paise per unit of foreign currency)
    for a given currency and transaction type at a specific branch location.

    Business rules:
    - The discount range (min–max) is location-specific.
    - The negotiated rate must always be BETWEEN the min and max (inclusive).
    - The total discount offered must NEVER exceed 0.20 paise per unit.
    - If the customer requests a discount greater than the max or greater than 0.20 paise,
      offer the maximum permissible discount instead.
    - The negotiated midpoint is recommended as the starting offer.

    Use this tool when a customer negotiates on the forex rate or asks for a better rate.
    """
    args = {
        "currency": currency,
        "transaction_type": transaction_type,
        "customer_city": customer_city,
        "requested_discount_paise": requested_discount_paise,
    }

    HARD_CAP_PAISE = 0.20   # Absolute maximum discount allowed — 0.20 paise

    # ── Per-location discount ranges (paise per unit of foreign currency) ──────
    # Format: city → {currency → {tx_type → (min_paise, max_paise)}}
    # If city not found, DEFAULT_RANGES are used.
    CITY_DISCOUNT_RANGES = {
        "mumbai": {
            "SGD": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
            "EUR": {"buy": (0.05, 0.20), "sell": (0.05, 0.18)},
            "USD": {"buy": (0.05, 0.20), "sell": (0.05, 0.18)},
            "GBP": {"buy": (0.05, 0.20), "sell": (0.05, 0.18)},
            "AED": {"buy": (0.03, 0.12), "sell": (0.03, 0.10)},
            "THB": {"buy": (0.02, 0.10), "sell": (0.02, 0.08)},
            "AUD": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
            "CAD": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
            "JPY": {"buy": (0.01, 0.05), "sell": (0.01, 0.04)},
            "CHF": {"buy": (0.05, 0.20), "sell": (0.05, 0.18)},
            "MYR": {"buy": (0.03, 0.15), "sell": (0.03, 0.12)},
        },
        "delhi": {
            "SGD": {"buy": (0.05, 0.17), "sell": (0.05, 0.14)},
            "EUR": {"buy": (0.05, 0.20), "sell": (0.05, 0.17)},
            "USD": {"buy": (0.05, 0.20), "sell": (0.05, 0.17)},
            "GBP": {"buy": (0.05, 0.20), "sell": (0.05, 0.17)},
            "AED": {"buy": (0.03, 0.12), "sell": (0.03, 0.10)},
            "THB": {"buy": (0.02, 0.09), "sell": (0.02, 0.08)},
            "AUD": {"buy": (0.05, 0.17), "sell": (0.05, 0.14)},
            "CAD": {"buy": (0.05, 0.17), "sell": (0.05, 0.14)},
            "JPY": {"buy": (0.01, 0.05), "sell": (0.01, 0.04)},
            "CHF": {"buy": (0.05, 0.20), "sell": (0.05, 0.17)},
            "MYR": {"buy": (0.03, 0.14), "sell": (0.03, 0.11)},
        },
        "bangalore": {
            "SGD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "EUR": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "USD": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "GBP": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "AED": {"buy": (0.03, 0.11), "sell": (0.03, 0.09)},
            "THB": {"buy": (0.02, 0.09), "sell": (0.02, 0.07)},
            "AUD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "CAD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "JPY": {"buy": (0.01, 0.04), "sell": (0.01, 0.03)},
            "CHF": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "MYR": {"buy": (0.03, 0.13), "sell": (0.03, 0.10)},
        },        "hyderabad": {
            "SGD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "EUR": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "USD": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "GBP": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "AED": {"buy": (0.03, 0.11), "sell": (0.03, 0.09)},
            "THB": {"buy": (0.02, 0.09), "sell": (0.02, 0.07)},
            "AUD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "CAD": {"buy": (0.05, 0.16), "sell": (0.04, 0.13)},
            "JPY": {"buy": (0.01, 0.04), "sell": (0.01, 0.03)},
            "CHF": {"buy": (0.05, 0.19), "sell": (0.04, 0.16)},
            "MYR": {"buy": (0.03, 0.13), "sell": (0.03, 0.10)},
        },
    }

    # Default ranges used when city is not in the map
    DEFAULT_RANGES = {
        "SGD": {"buy": (0.04, 0.15), "sell": (0.04, 0.12)},
        "EUR": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
        "USD": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
        "GBP": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
        "AED": {"buy": (0.03, 0.10), "sell": (0.03, 0.08)},
        "THB": {"buy": (0.02, 0.08), "sell": (0.02, 0.06)},
        "AUD": {"buy": (0.04, 0.15), "sell": (0.04, 0.12)},
        "CAD": {"buy": (0.04, 0.15), "sell": (0.04, 0.12)},
        "JPY": {"buy": (0.01, 0.04), "sell": (0.01, 0.03)},
        "CHF": {"buy": (0.05, 0.18), "sell": (0.05, 0.15)},
        "MYR": {"buy": (0.03, 0.12), "sell": (0.03, 0.10)},
        "NZD": {"buy": (0.04, 0.15), "sell": (0.04, 0.12)},
        "IDR": {"buy": (0.01, 0.05), "sell": (0.01, 0.04)},
    }

    ccy   = currency.upper().strip()
    tx    = transaction_type.lower().strip()
    city  = customer_city.lower().strip()

    # Resolve discount range
    city_ranges = CITY_DISCOUNT_RANGES.get(city, {})
    ccy_ranges  = city_ranges.get(ccy) or DEFAULT_RANGES.get(ccy)

    if not ccy_ranges:
        result = {
            "success": False,
            "currency": ccy,
            "transaction_type": transaction_type,
            "message": (
                f"Discount range data not available for {ccy}. "
                "Please contact the branch for rate negotiation on this currency."
            ),
        }
        _log("check_forex_discount_range", args, result)
        return result

    range_pair = ccy_ranges.get(tx) or ccy_ranges.get("buy")   # fallback to buy range
    min_discount, max_discount = range_pair

    # Hard cap at 0.20 paise — never allow more
    max_discount = min(max_discount, HARD_CAP_PAISE)

    # Negotiated midpoint (recommended starting offer)
    negotiated_discount = round((min_discount + max_discount) / 2, 4)

    # Evaluate customer's requested discount
    customer_verdict = "not_requested"
    offered_discount = negotiated_discount

    if requested_discount_paise > 0:
        if requested_discount_paise > max_discount:
            customer_verdict = "exceeds_limit"
            offered_discount = max_discount          # cap at max
            negotiation_message = (
                f"The requested discount of {requested_discount_paise:.4f} paise exceeds the maximum "
                f"permissible limit of {max_discount:.4f} paise for {ccy} ({transaction_type}). "
                f"The best we can offer is {max_discount:.4f} paise per unit."
            )
        elif requested_discount_paise < min_discount:
            customer_verdict = "below_minimum"
            offered_discount = min_discount
            negotiation_message = (
                f"The requested discount of {requested_discount_paise:.4f} paise is below the minimum "
                f"threshold of {min_discount:.4f} paise. We can offer the minimum of {min_discount:.4f} paise."
            )
        else:
            customer_verdict = "within_range"
            offered_discount = round(requested_discount_paise, 4)
            negotiation_message = (
                f"The requested discount of {requested_discount_paise:.4f} paise is within the "
                f"permissible range ({min_discount:.4f}–{max_discount:.4f} paise). Discount approved."
            )
    else:
        negotiation_message = (
            f"Recommended negotiated discount for {ccy} ({transaction_type}) "
            f"in {customer_city or 'your city'}: {negotiated_discount:.4f} paise per unit. "
            f"Permissible range: {min_discount:.4f}–{max_discount:.4f} paise."
        )

    result = {
        "success": True,
        "currency": ccy,
        "transaction_type": transaction_type,
        "customer_city": customer_city,
        "min_discount_paise": min_discount,
        "max_discount_paise": max_discount,
        "hard_cap_paise": HARD_CAP_PAISE,
        "negotiated_midpoint_paise": negotiated_discount,
        "offered_discount_paise": offered_discount,
        "customer_verdict": customer_verdict,
        "negotiation_message": negotiation_message,
        "message": negotiation_message,
        "note": (
            "Discount is in paise per unit of foreign currency. "
            f"Maximum allowed discount is {HARD_CAP_PAISE} paise as per policy. "
            "Final rate is subject to branch manager approval."
        ),
    }
    _log("check_forex_discount_range", args, result)
    return result

"""Google Maps Logistics/ETA service (§8.4).

Server-side API call using the company-held API key.
No household consent required — this is a Skill, not a Connector.
The string 'google_maps' should appear only in this file (architecture §9.1 ★).

Edge cases handled:
- Location unresolvable → fall back to reminder skill
- Google Maps outage → fall back with larger default buffer
- Implausible travel time (>2 hours for short trip) → sanity cap
- Item created less than lead time before start → fire immediately
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.platform.config import get_settings
from app.platform.observability import get_logger

logger = get_logger(__name__)
_settings = get_settings()

MAX_PLAUSIBLE_MINUTES = 120  # >2 hours is likely a geocoding error
DEFAULT_BUFFER_MINUTES = 10   # Added to travel time (§8.4)


async def compute_eta(
    origin: str,
    destination: str,
    arrival_time: datetime,
) -> dict:
    """Call Google Maps Routes API and compute leave_by_at.

    Returns:
        {
            eta_minutes: int,
            leave_by_at: datetime,
            traffic_condition: str,  # light | moderate | heavy
            distance_km: float,
        }

    Raises:
        ValueError on unresolvable location or API error (caller falls back to reminder).
    """
    if not _settings.google_maps_api_key:
        raise ValueError("Google Maps API key not configured")

    import googlemaps
    from datetime import timedelta

    gmaps = googlemaps.Client(key=_settings.google_maps_api_key)

    try:
        # Departure time: work backwards from arrival — try 90 minutes before as the window
        departure_estimate = arrival_time - timedelta(hours=2)
        now = datetime.now(timezone.utc)
        dep_time = max(departure_estimate, now + timedelta(minutes=1))

        result = gmaps.distance_matrix(
            origins=[origin],
            destinations=[destination],
            mode="driving",
            departure_time=dep_time,
            traffic_model="best_guess",
        )

        row = result["rows"][0]["elements"][0]
        if row["status"] != "OK":
            raise ValueError(f"Google Maps returned status: {row['status']}")

        duration_in_traffic = row.get("duration_in_traffic", row["duration"])
        eta_seconds = duration_in_traffic["value"]
        eta_minutes = int(eta_seconds / 60)
        distance_m = row["distance"]["value"]

        # Sanity check: implausible duration
        if eta_minutes > MAX_PLAUSIBLE_MINUTES:
            logger.warning(
                "implausible_eta",
                eta_minutes=eta_minutes,
                origin=origin[:30],
                destination=destination[:30],
            )
            raise ValueError(f"Implausible travel time: {eta_minutes} minutes")

        # Traffic condition
        base_duration = row["duration"]["value"] / 60
        ratio = eta_minutes / max(base_duration, 1)
        if ratio < 1.2:
            traffic = "light"
        elif ratio < 1.5:
            traffic = "moderate"
        else:
            traffic = "heavy"

        leave_by_at = arrival_time - timedelta(minutes=eta_minutes + DEFAULT_BUFFER_MINUTES)

        return {
            "eta_minutes": eta_minutes,
            "leave_by_at": leave_by_at,
            "traffic_condition": traffic,
            "distance_km": round(distance_m / 1000, 1),
        }

    except googlemaps.exceptions.ApiError as e:
        logger.error("google_maps_api_error", error=str(e))
        raise ValueError(f"Google Maps API error: {e}")
    except Exception as e:
        if "Implausible" in str(e) or "status" in str(e):
            raise
        logger.error("google_maps_unexpected_error", error=str(e))
        raise ValueError(f"Google Maps unexpected error: {e}")

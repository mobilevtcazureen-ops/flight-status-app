#!/usr/bin/env python3
"""Local server for the flight status app.

Serves the static web UI and exposes /api/status, which queries the
AeroDataBox API (via RapidAPI) for a flight's live status. The API key
never reaches the browser.

Start:
    export AERODATABOX_API_KEY="your_rapidapi_key"
    python3 server.py

Then open http://localhost:8000
"""

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "8000"))
STATIC_DIR = Path(__file__).parent / "static"
CONFIG_FILE = Path(__file__).parent / "config.json"

RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
OSRM_HOST = "router.project-osrm.org"
FLIGHT_NUMBER_RE = re.compile(r"^[A-Za-z0-9]{2,8}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ICAO_RE = re.compile(r"^[A-Za-z0-9]{3,4}$")

# Airport coordinates rarely change, so cache them for the life of the process.
airport_location_cache = {}

STATUS_LABELS = {
    "unknown": ("Status Unknown", "gray"),
    "expected": ("Scheduled", "blue"),
    "checkin": ("Check-In", "blue"),
    "boarding": ("Boarding", "orange"),
    "gateclosed": ("Gate Closed", "orange"),
    "gateopen": ("Gate Open", "orange"),
    "departed": ("Departed", "teal"),
    "enroute": ("En Route", "teal"),
    "approaching": ("Approaching", "teal"),
    "arrived": ("Landed", "green"),
    "landed": ("Landed", "green"),
    "delayed": ("Delayed", "red"),
    "diverted": ("Diverted", "red"),
    "canceled": ("Canceled", "red"),
    "canceleduncertain": ("Canceled", "red"),
    "cancelled": ("Canceled", "red"),
}


def load_api_key():
    key = os.environ.get("AERODATABOX_API_KEY")
    if key:
        return key.strip()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            key = data.get("AERODATABOX_API_KEY")
            if key:
                return key.strip()
        except (json.JSONDecodeError, OSError):
            pass
    return None


def status_label(raw_status):
    if not raw_status:
        return STATUS_LABELS["unknown"]
    key = re.sub(r"[^a-z]", "", raw_status.lower())
    return STATUS_LABELS.get(key, (raw_status, "gray"))


def pick_time(node):
    if not node:
        return None
    return node.get("local") or node.get("utc")


def normalize_endpoint(node):
    if not node:
        return None
    airport = node.get("airport", {}) or {}
    scheduled = pick_time(node.get("scheduledTime"))
    # Prefer an actual revision, fall back to the predicted/runway estimate
    # so we still surface an updated ETA even before a formal delay is filed.
    effective = (
        pick_time(node.get("revisedTime"))
        or pick_time(node.get("predictedTime"))
        or pick_time(node.get("runwayTime"))
    )
    location = airport.get("location") or {}
    return {
        "airportName": airport.get("name") or airport.get("shortName"),
        "iata": airport.get("iata"),
        "icao": airport.get("icao"),
        "municipality": airport.get("municipalityName"),
        "lat": location.get("lat"),
        "lon": location.get("lon"),
        "terminal": node.get("terminal"),
        "gate": node.get("gate"),
        "scheduledTime": scheduled,
        "revisedTime": effective,
        "delayed": bool(scheduled and effective and scheduled != effective),
    }


def normalize_flight(raw):
    airline = raw.get("airline", {}) or {}
    aircraft = raw.get("aircraft", {}) or {}
    label, color = status_label(raw.get("status"))
    return {
        "number": raw.get("number") or raw.get("callSign"),
        "airline": airline.get("name"),
        "statusLabel": label,
        "statusColor": color,
        "statusRaw": raw.get("status"),
        "aircraftModel": aircraft.get("model"),
        "aircraftReg": aircraft.get("reg"),
        "aircraftModeS": aircraft.get("modeS"),
        "departure": normalize_endpoint(raw.get("departure")),
        "arrival": normalize_endpoint(raw.get("arrival")),
    }


def fetch_flight_status(flight_number, date, api_key):
    if date:
        path = f"/flights/number/{flight_number}/{date}"
    else:
        path = f"/flights/number/{flight_number}"
    url = f"https://{RAPIDAPI_HOST}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
            "User-Agent": "curl/8.4.0",
            "Accept": "application/json",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def fetch_airport_location(icao, api_key):
    if icao in airport_location_cache:
        return airport_location_cache[icao]

    url = f"https://{RAPIDAPI_HOST}/airports/icao/{icao}"
    req = urllib.request.Request(
        url,
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
            "User-Agent": "curl/8.4.0",
            "Accept": "application/json",
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    location = data.get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")
    if lat is not None and lon is not None:
        airport_location_cache[icao] = (lat, lon)
    return (lat, lon) if lat is not None and lon is not None else None


def fetch_driving_duration(origin_lat, origin_lon, dest_lat, dest_lon):
    url = (
        f"https://{OSRM_HOST}/route/v1/driving/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=false"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.4.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    routes = data.get("routes") or []
    if not routes:
        return None
    return {
        "durationSeconds": routes[0]["duration"],
        "distanceMeters": routes[0]["distance"],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/status":
            return self.handle_status(parsed)

        if parsed.path == "/api/travel-time":
            return self.handle_travel_time(parsed)

        if parsed.path == "/":
            return self.serve_file("index.html", "text/html; charset=utf-8")

        # static assets
        safe_path = parsed.path.lstrip("/")
        return self.serve_file(safe_path, None)

    def handle_status(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        flight_number = (qs.get("flight", [""])[0] or "").strip().upper()
        date = (qs.get("date", [""])[0] or "").strip()

        if not FLIGHT_NUMBER_RE.match(flight_number):
            return self.send_json(400, {
                "error": "invalid_flight_number",
                "message": "Invalid flight number. Example: AA1780",
            })
        if date and not DATE_RE.match(date):
            return self.send_json(400, {
                "error": "invalid_date",
                "message": "Invalid date. Expected format: YYYY-MM-DD",
            })

        api_key = load_api_key()
        if not api_key:
            return self.send_json(200, {
                "error": "missing_api_key",
                "message": (
                    "No API key configured. Create a free account at "
                    "rapidapi.com/aedbx-aedbx/api/aerodatabox, then set the "
                    "AERODATABOX_API_KEY environment variable, or create a "
                    "config.json file next to server.py."
                ),
            })

        try:
            raw = fetch_flight_status(flight_number, date, api_key)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return self.send_json(200, {
                    "error": "not_found",
                    "message": f"No flight found for {flight_number}.",
                })
            if e.code in (401, 403):
                return self.send_json(200, {
                    "error": "invalid_api_key",
                    "message": "API key rejected. Check AERODATABOX_API_KEY.",
                })
            if e.code == 429:
                return self.send_json(200, {
                    "error": "rate_limited",
                    "message": "Too many requests. Try again in a few minutes.",
                })
            return self.send_json(502, {
                "error": "upstream_error",
                "message": f"Data provider error ({e.code}).",
            })
        except urllib.error.URLError:
            return self.send_json(502, {
                "error": "network_error",
                "message": "Unable to reach the flight status service.",
            })

        if not raw:
            return self.send_json(200, {
                "error": "not_found",
                "message": f"No flight found for {flight_number}.",
            })

        flights = [normalize_flight(f) for f in raw]
        return self.send_json(200, {"flights": flights})

    def handle_travel_time(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        icao = (qs.get("icao", [""])[0] or "").strip().upper()

        try:
            user_lat = float(qs.get("lat", [""])[0])
            user_lon = float(qs.get("lon", [""])[0])
        except (TypeError, ValueError):
            return self.send_json(400, {
                "error": "invalid_location",
                "message": "Missing or invalid location.",
            })

        if not ICAO_RE.match(icao):
            return self.send_json(400, {
                "error": "invalid_airport",
                "message": "Missing or invalid airport code.",
            })
        if not (-90 <= user_lat <= 90 and -180 <= user_lon <= 180):
            return self.send_json(400, {
                "error": "invalid_location",
                "message": "Location out of range.",
            })

        api_key = load_api_key()
        if not api_key:
            return self.send_json(200, {
                "error": "missing_api_key",
                "message": "No API key configured.",
            })

        try:
            location = fetch_airport_location(icao, api_key)
        except (urllib.error.HTTPError, urllib.error.URLError):
            return self.send_json(502, {
                "error": "network_error",
                "message": "Unable to look up the airport location.",
            })
        if not location:
            return self.send_json(200, {
                "error": "not_found",
                "message": "Airport location unavailable.",
            })
        airport_lat, airport_lon = location

        try:
            route = fetch_driving_duration(user_lat, user_lon, airport_lat, airport_lon)
        except (urllib.error.HTTPError, urllib.error.URLError):
            return self.send_json(502, {
                "error": "network_error",
                "message": "Unable to calculate the drive time.",
            })
        if not route:
            return self.send_json(200, {
                "error": "not_found",
                "message": "No driving route found.",
            })

        return self.send_json(200, {
            "durationSeconds": route["durationSeconds"],
            "distanceMeters": route["distanceMeters"],
        })

    def serve_file(self, rel_path, content_type):
        if not rel_path:
            rel_path = "index.html"
        file_path = (STATIC_DIR / rel_path).resolve()
        if STATIC_DIR.resolve() not in file_path.parents and file_path != STATIC_DIR.resolve():
            self.send_error(403)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return

        if content_type is None:
            ext = file_path.suffix.lower()
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }.get(ext, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Flight status available at http://localhost:{PORT}")
    if not load_api_key():
        print(
            "Warning: AERODATABOX_API_KEY is not set. "
            "The app will show setup instructions instead."
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

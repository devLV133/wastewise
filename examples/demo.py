"""Add three synthetic bins and print a plan. Run against the local server."""

import json
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

BASE_URL = "http://127.0.0.1:8000"


def post(path, body):
    request = Request(
        BASE_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    suffix = uuid4().hex[:8]
    for label, latitude, fill, priority in [
        ("Market", 53.4808, 90, None),
        ("Park", 53.4880, 30, "urgent"),
        ("Station", 53.4770, 60, "normal"),
    ]:
        item = post("/bins", {
            "label": f"Demo {label} {suffix}",
            "location": f"Synthetic {label} location, Manchester",
            "latitude": latitude,
            "longitude": -2.2426,
            "waste_type": "general",
            "capacity_litres": 240,
            "fill_percent": fill,
        })
        if priority:
            post("/requests", {"bin_id": item["id"], "priority": priority})

    plan = post("/plans", {
        "waste_type": "general", "depot_latitude": 53.475,
        "depot_longitude": -2.25, "fill_threshold": 80, "max_stops": 20,
    })
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    try:
        main()
    except URLError as error:
        raise SystemExit(f"Could not run demo: {error}. Check that WasteWise is running.") from error

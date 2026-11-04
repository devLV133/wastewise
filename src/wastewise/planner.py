"""Priority-first, nearest-neighbour ordering; not road-route optimisation."""

from math import asin, cos, radians, sin, sqrt

from .models import Bin, CollectionPlan, PlanCreate, PlanStop


def distance_km(start: tuple[float, float], end: tuple[float, float]) -> float:
    lat1, lon1 = map(radians, start)
    lat2, lon2 = map(radians, end)
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(min(1.0, max(0.0, h))))


def build_plan(candidates: list[dict], options: PlanCreate) -> CollectionPlan:
    depot = (options.depot_latitude, options.depot_longitude)
    current = depot
    remaining = list(candidates)
    stops = []
    total_distance = 0.0
    total_litres = 0.0

    def position(row):
        return row["latitude"], row["longitude"]

    def priority(row):
        if row["priority"] == "urgent":
            return 0
        return 1 if row["request_id"] is not None else 2

    while remaining and len(stops) < options.max_stops:
        selected = min(
            remaining,
            key=lambda row: (priority(row), distance_km(current, position(row)), row["id"]),
        )
        leg = distance_km(current, position(selected))
        litres = selected["capacity_litres"] * selected["fill_percent"] / 100
        reason = (
            f"{selected['priority']}_request"
            if selected["request_id"] is not None
            else "fill_threshold"
        )
        stops.append(PlanStop(
            sequence=len(stops) + 1,
            bin=Bin(**{key: selected[key] for key in Bin.model_fields}),
            reason=reason,
            request_id=selected["request_id"],
            distance_from_previous_km=round(leg, 3),
            estimated_litres=round(litres, 2),
        ))
        total_distance += leg
        total_litres += litres
        current = position(selected)
        remaining.remove(selected)

    return_leg = distance_km(current, depot) if stops else 0.0
    return CollectionPlan(
        waste_type=options.waste_type,
        stops=stops,
        total_estimated_litres=round(total_litres, 2),
        return_to_depot_km=round(return_leg, 3),
        total_distance_km=round(total_distance + return_leg, 3),
    )

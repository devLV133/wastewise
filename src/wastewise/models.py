"""Validated request and response models for the public API."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class WasteType(str, Enum):
    GENERAL = "general"
    RECYCLING = "recycling"
    ORGANIC = "organic"


class Priority(str, Enum):
    NORMAL = "normal"
    URGENT = "urgent"


class RequestStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class APIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class BinCreate(APIModel):
    label: str = Field(min_length=1, max_length=80)
    location: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    waste_type: WasteType
    capacity_litres: float = Field(gt=0, le=100_000)
    fill_percent: float = Field(default=0, ge=0, le=100)


class Bin(BinCreate):
    id: int
    created_at: datetime


class FillUpdate(APIModel):
    fill_percent: float = Field(ge=0, le=100)


class PickupCreate(APIModel):
    bin_id: int = Field(gt=0)
    priority: Priority = Priority.NORMAL
    notes: str = Field(default="", max_length=500)


class Pickup(PickupCreate):
    id: int
    status: RequestStatus
    created_at: datetime
    closed_at: datetime | None


class Collection(APIModel):
    id: int
    bin_id: int
    collected_at: datetime
    estimated_litres: float


class PlanCreate(APIModel):
    waste_type: WasteType
    depot_latitude: float = Field(ge=-90, le=90)
    depot_longitude: float = Field(ge=-180, le=180)
    fill_threshold: float = Field(default=80, ge=0, le=100)
    max_stops: int = Field(default=20, ge=1, le=100)


class PlanStop(APIModel):
    sequence: int
    bin: Bin
    reason: str
    request_id: int | None
    distance_from_previous_km: float
    estimated_litres: float


class CollectionPlan(APIModel):
    waste_type: WasteType
    stops: list[PlanStop]
    total_estimated_litres: float
    return_to_depot_km: float
    total_distance_km: float
    distance_method: str = "haversine_straight_line"


class Summary(APIModel):
    total_bins: int
    bins_at_or_above_80_percent: int
    pending_requests: int
    completed_collections: int
    estimated_litres_collected: float

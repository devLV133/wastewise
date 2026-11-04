"""HTTP endpoints. Run with: uvicorn wastewise.main:app --reload."""

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from .db import connect, initialize
from .models import (
    Bin, BinCreate, Collection, CollectionPlan, FillUpdate, Pickup,
    PickupCreate, PlanCreate, RequestStatus, Summary, WasteType,
)
from .planner import build_plan


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database(request: Request):
    with connect(request.app.state.db_path) as connection:
        yield connection


DB = Annotated[sqlite3.Connection, Depends(database)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def require_row(db: sqlite3.Connection, query: str, item_id: int):
    row = db.execute(query, (item_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Item not found")
    return dict(row)


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("WASTE_DB_PATH", "data/wastewise.db"))
    if str(path) == ":memory:":
        raise ValueError("Use a file path; per-request connections require file-backed SQLite.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(path)
        yield

    app = FastAPI(
        title="WasteWise",
        description="Track bins, request pickups, and plan waste collections.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db_path = path

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/health", tags=["Service"])
    def health(db: DB):
        db.execute("SELECT COUNT(*) FROM bins").fetchone()
        return {"status": "ok"}

    @app.post("/bins", response_model=Bin, status_code=201, tags=["Bins"])
    def create_bin(payload: BinCreate, db: DB):
        values = payload.model_dump(mode="json")
        values["created_at"] = utc_now()
        try:
            cursor = db.execute(
                """INSERT INTO bins
                (label, location, latitude, longitude, waste_type,
                 capacity_litres, fill_percent, created_at)
                VALUES (:label, :location, :latitude, :longitude, :waste_type,
                        :capacity_litres, :fill_percent, :created_at)""",
                values,
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "A bin with this label already exists") from error
        return require_row(db, "SELECT * FROM bins WHERE id = ?", cursor.lastrowid)

    @app.get("/bins", response_model=list[Bin], tags=["Bins"])
    def list_bins(
        db: DB, waste_type: WasteType | None = None,
        min_fill: Annotated[float, Query(ge=0, le=100)] = 0,
        limit: Limit = 100, offset: Offset = 0,
    ):
        rows = db.execute(
            """SELECT * FROM bins WHERE fill_percent >= ?
            AND (? IS NULL OR waste_type = ?) ORDER BY id LIMIT ? OFFSET ?""",
            (min_fill, waste_type, waste_type, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    @app.get("/bins/{bin_id}", response_model=Bin, tags=["Bins"])
    def get_bin(bin_id: int, db: DB):
        return require_row(db, "SELECT * FROM bins WHERE id = ?", bin_id)

    @app.patch("/bins/{bin_id}/fill", response_model=Bin, tags=["Bins"])
    def update_fill(bin_id: int, payload: FillUpdate, db: DB):
        cursor = db.execute(
            "UPDATE bins SET fill_percent = ? WHERE id = ?", (payload.fill_percent, bin_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Bin not found")
        return require_row(db, "SELECT * FROM bins WHERE id = ?", bin_id)

    @app.post("/requests", response_model=Pickup, status_code=201, tags=["Pickups"])
    def create_request(payload: PickupCreate, db: DB):
        require_row(db, "SELECT * FROM bins WHERE id = ?", payload.bin_id)
        try:
            cursor = db.execute(
                """INSERT INTO pickup_requests
                (bin_id, priority, notes, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)""",
                (payload.bin_id, payload.priority, payload.notes, utc_now()),
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "This bin already has a pending pickup request") from error
        return require_row(db, "SELECT * FROM pickup_requests WHERE id = ?", cursor.lastrowid)

    @app.get("/requests", response_model=list[Pickup], tags=["Pickups"])
    def list_requests(
        db: DB, status: RequestStatus | None = None,
        limit: Limit = 100, offset: Offset = 0,
    ):
        rows = db.execute(
            """SELECT * FROM pickup_requests WHERE (? IS NULL OR status = ?)
            ORDER BY id DESC LIMIT ? OFFSET ?""", (status, status, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    @app.post("/requests/{request_id}/cancel", response_model=Pickup, tags=["Pickups"])
    def cancel_request(request_id: int, db: DB):
        cursor = db.execute(
            """UPDATE pickup_requests SET status = 'cancelled', closed_at = ?
            WHERE id = ? AND status = 'pending'""", (utc_now(), request_id),
        )
        result = require_row(db, "SELECT * FROM pickup_requests WHERE id = ?", request_id)
        if cursor.rowcount == 0:
            raise HTTPException(409, "Only pending pickup requests can be cancelled")
        return result

    @app.post("/bins/{bin_id}/collect", response_model=Collection, status_code=201,
              tags=["Collections"])
    def collect_bin(bin_id: int, db: DB):
        # Lock before reading so concurrent collection/fill writes cannot use stale data.
        db.execute("BEGIN IMMEDIATE")
        bin_record = require_row(db, "SELECT * FROM bins WHERE id = ?", bin_id)
        pending = db.execute(
            "SELECT id FROM pickup_requests WHERE bin_id = ? AND status = 'pending'",
            (bin_id,),
        ).fetchone()
        if bin_record["fill_percent"] == 0 and pending is None:
            raise HTTPException(409, "This bin is empty and has no pending pickup request")
        timestamp = utc_now()
        litres = round(bin_record["capacity_litres"] * bin_record["fill_percent"] / 100, 2)
        cursor = db.execute(
            "INSERT INTO collections (bin_id, collected_at, estimated_litres) VALUES (?, ?, ?)",
            (bin_id, timestamp, litres),
        )
        db.execute("UPDATE bins SET fill_percent = 0 WHERE id = ?", (bin_id,))
        db.execute(
            """UPDATE pickup_requests SET status = 'completed', closed_at = ?
            WHERE bin_id = ? AND status = 'pending'""", (timestamp, bin_id),
        )
        return require_row(db, "SELECT * FROM collections WHERE id = ?", cursor.lastrowid)

    @app.get("/collections", response_model=list[Collection], tags=["Collections"])
    def list_collections(db: DB, limit: Limit = 100, offset: Offset = 0):
        return [dict(row) for row in db.execute(
            "SELECT * FROM collections ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()]

    @app.post("/plans", response_model=CollectionPlan, tags=["Planning"])
    def create_plan(payload: PlanCreate, db: DB):
        rows = db.execute(
            """SELECT b.*, r.id AS request_id, r.priority FROM bins b
            LEFT JOIN pickup_requests r ON r.bin_id = b.id AND r.status = 'pending'
            WHERE b.waste_type = ? AND (b.fill_percent >= ? OR r.id IS NOT NULL)""",
            (payload.waste_type, payload.fill_threshold),
        ).fetchall()
        return build_plan([dict(row) for row in rows], payload)

    @app.get("/summary", response_model=Summary, tags=["Service"])
    def summary(db: DB):
        return dict(db.execute(
            """SELECT
            (SELECT COUNT(*) FROM bins) AS total_bins,
            (SELECT COUNT(*) FROM bins WHERE fill_percent >= 80)
                AS bins_at_or_above_80_percent,
            (SELECT COUNT(*) FROM pickup_requests WHERE status = 'pending') AS pending_requests,
            (SELECT COUNT(*) FROM collections) AS completed_collections,
            (SELECT COALESCE(SUM(estimated_litres), 0) FROM collections)
                AS estimated_litres_collected"""
        ).fetchone())

    return app


app = create_app()

# WasteWise

A Python waste collection service for tracking bins, managing pickup requests, and preparing collection plans.

Built with **FastAPI**, **Pydantic**, and **SQLite**. Runs locally without an API key or external database. The interactive API at `/docs` is the interface; no separate frontend is required.

## What it does

- Register bins with their location, waste type, capacity, and fill percentage.
- Update fill levels and filter bins by waste type or minimum fill.
- Create normal or urgent pickup requests, with one pending request per bin.
- Suggest collection stops for one waste type at a time.
- Record collections, reset fill levels, and close pending requests in one database transaction.
- View collection history and service totals.

Supported waste types: `general`, `recycling`, and `organic`.

## Run locally

Requires Python 3.11 or newer. Open a terminal in this repository's root folder.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn wastewise.main:app --reload
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.

Open **http://127.0.0.1:8000/docs**. Expand an endpoint, select **Try it out**, enter the request, and select **Execute**.

The SQLite database is created automatically at `data/wastewise.db`, relative to the directory where the server starts. Data survives restarts. Set `WASTE_DB_PATH` to use a different file path. In-memory databases are not supported because requests use separate connections.

For a quick example, leave the server running and use a second terminal with the same virtual environment:

```bash
python examples/demo.py
```

Each run adds three synthetic Manchester bins and two pickup requests, then prints a collection plan. The example locations and fill levels are invented. Existing eligible bins are included in the plan as well.

## Example workflow

### 1. Register a bin

```bash
curl -X POST http://127.0.0.1:8000/bins \
  -H "Content-Type: application/json" \
  -d '{"label":"Market-01","location":"Example Market, Manchester","latitude":53.4808,"longitude":-2.2426,"waste_type":"general","capacity_litres":240,"fill_percent":85}'
```

The response includes the bin's `id`. Use that value below; these examples assume `1`.

### 2. Request an urgent pickup

```bash
curl -X POST http://127.0.0.1:8000/requests \
  -H "Content-Type: application/json" \
  -d '{"bin_id":1,"priority":"urgent","notes":"Reported near capacity"}'
```

### 3. Preview a collection plan

```bash
curl -X POST http://127.0.0.1:8000/plans \
  -H "Content-Type: application/json" \
  -d '{"waste_type":"general","depot_latitude":53.475,"depot_longitude":-2.25,"fill_threshold":80,"max_stops":20}'
```

### 4. Record the collection

```bash
curl -X POST http://127.0.0.1:8000/bins/1/collect
curl http://127.0.0.1:8000/summary
```

The bin's fill becomes zero, its pending request becomes `completed`, and collection history records **204 estimated litres**: 240 × 85 / 100. All three changes succeed or fail together. Volume is estimated from the reported fill level, not weighed or measured at collection.

## API reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check the service and database |
| POST | `/bins` | Register a bin |
| GET | `/bins` | List bins; filter with `waste_type` and `min_fill` |
| GET | `/bins/{bin_id}` | Read one bin |
| PATCH | `/bins/{bin_id}/fill` | Set `fill_percent` |
| POST | `/requests` | Request a pickup |
| GET | `/requests` | List requests; filter with `status` |
| POST | `/requests/{request_id}/cancel` | Cancel a pending request |
| POST | `/bins/{bin_id}/collect` | Record a collection |
| GET | `/collections` | Read collection history |
| POST | `/plans` | Preview collection stops without changing records |
| GET | `/summary` | Read totals |

List endpoints accept `limit` (1–100, default 100) and `offset` (default 0). Bins are ordered by ascending ID; requests and collections by descending ID. Bin labels are unique and case-sensitive; surrounding whitespace is removed.

Errors use standard HTTP status codes: `404` for missing records, `409` for conflicts such as duplicate requests, and `422` for invalid input. Request status is `pending`, `completed`, or `cancelled`. Completed and cancelled requests cannot be cancelled again. A new request may be created once the previous one is closed.

Collecting an empty bin without a pending request returns `409` to prevent accidental duplicate records. An empty bin with a pending request may be collected to close the request with zero estimated litres.

## How planning works

The planner selects bins of the requested waste type that either meet the fill threshold or have a pending request. It visits urgent requests first, then normal requests, then bins included only because of their fill level. Within each group, it picks the closest remaining bin from the current position. Equal distances are resolved by bin ID for consistent output.

`max_stops` limits the number of visits. Distances use the Haversine formula, and `total_distance_km` includes the return to the depot. Displayed values are rounded, so rounded legs may differ slightly from the rounded total.

**This is a simple planning heuristic.** Distances are straight-line estimates, not driving distances. Plans do not account for road access, traffic, truck capacity, working hours, or multiple vehicles, and are not guaranteed to be shortest. A plan is a preview, not a reservation or dispatch. Regenerate it when bin data changes.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use temporary databases. They cover validation, persistence, request conflicts, collection rollback, concurrent collection attempts, filtering, planning order, waste separation, distances, and summary totals.

GitHub Actions is configured to run the suite on Python 3.11, 3.12, and 3.13 for pushes and pull requests. The project was checked locally on Python 3.12; other versions are verified when the workflow runs.

## Project layout

| Path | Contents |
|---|---|
| `src/wastewise/main.py` | API endpoints and application factory |
| `src/wastewise/models.py` | Input validation and response models |
| `src/wastewise/db.py` | SQLite schema and connection handling |
| `src/wastewise/planner.py` | Stop ordering and distance calculation |
| `examples/demo.py` | Runnable synthetic example |
| `tests/test_api.py` | Behaviour and integration tests |
| `.github/workflows/tests.yml` | Continuous integration |

## Put it on GitHub

If you downloaded the ZIP, extract it and open a terminal inside `wastewise`. The ZIP contains source files, not Git history. With the [GitHub CLI](https://cli.github.com/) installed:

```bash
git init -b main
git add .
git commit -m "Initial WasteWise implementation"
gh auth login
gh repo create wastewise --private --source=. --remote=origin --push
```

If you already have this as a Git repository, skip the first three commands. Use a different repository name if `wastewise` already exists in your account. The command above creates a private repository; change `--private` to `--public` if you want it public.

## Scope

This is a local prototype and portfolio starter. Fill levels are entered manually or submitted by an API client. There are no real sensor feeds, user accounts, permissions, or production deployment configuration. Before shared deployment, add authentication, access controls, backups, and database migrations. Keep the default local binding while experimenting.

## References and licence

- [FastAPI testing documentation](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI application lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [Python SQLite documentation](https://docs.python.org/3/library/sqlite3.html)

MIT licence. See [LICENSE](LICENSE).

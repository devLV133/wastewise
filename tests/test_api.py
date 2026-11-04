"""Behaviour tests against a fresh, temporary SQLite database."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from wastewise.main import create_app
from wastewise.planner import distance_km


class WasteWiseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "nested" / "test.db"
        self.client = self.enterContext(TestClient(create_app(self.db_path)))

    def bin(self, label="Central", **overrides):
        body = {
            "label": label,
            "location": "Example Street",
            "latitude": 53.4808,
            "longitude": -2.2426,
            "waste_type": "general",
            "capacity_litres": 240,
            "fill_percent": 90,
        }
        body.update(overrides)
        response = self.client.post("/bins", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def request(self, bin_id, priority="normal"):
        response = self.client.post("/requests", json={"bin_id": bin_id, "priority": priority})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def plan(self, **overrides):
        body = {"waste_type": "general", "depot_latitude": 53.4808, "depot_longitude": -2.2426}
        body.update(overrides)
        response = self.client.post("/plans", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health_docs_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("/plans", self.client.get("/openapi.json").json()["paths"])

    def test_create_read_and_update_bin(self):
        item = self.bin(label="  Market  ")
        self.assertEqual(item["label"], "Market")
        self.assertIsNotNone(item["created_at"])
        response = self.client.patch(f"/bins/{item['id']}/fill", json={"fill_percent": 40})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["fill_percent"], 40)

    def test_duplicate_bin_label_returns_conflict(self):
        self.bin()
        response = self.client.post("/bins", json={
            "label": "Central", "location": "Elsewhere", "latitude": 0, "longitude": 0,
            "capacity_litres": 120, "waste_type": "general",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.client.get("/bins").json()), 1)

    def test_bin_validation(self):
        valid = {
            "label": "Test", "location": "Street", "latitude": 0, "longitude": 0,
            "waste_type": "general", "capacity_litres": 240, "fill_percent": 80,
        }
        invalid_cases = [
            {"label": "   "}, {"location": ""}, {"latitude": 91}, {"longitude": -181},
            {"capacity_litres": 0}, {"fill_percent": -1}, {"fill_percent": 101},
            {"waste_type": "hazardous"}, {"latitude": "nan"}, {"extra_field": True},
        ]
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                self.assertEqual(self.client.post("/bins", json=valid | invalid).status_code, 422)

    def test_invalid_fill_does_not_change_state(self):
        item = self.bin()
        response = self.client.patch(f"/bins/{item['id']}/fill", json={"fill_percent": 101})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["fill_percent"], 90)

    def test_bin_filters_and_pagination(self):
        first = self.bin("A", fill_percent=10)
        second = self.bin("B", waste_type="recycling")
        self.bin("C", fill_percent=80)
        self.assertEqual(len(self.client.get("/bins?min_fill=80").json()), 2)
        self.assertEqual(self.client.get("/bins?waste_type=recycling").json()[0]["id"], second["id"])
        self.assertEqual(self.client.get("/bins?limit=1").json()[0]["id"], first["id"])
        self.assertEqual(self.client.get("/bins?limit=1&offset=1").json()[0]["id"], second["id"])
        self.assertEqual(self.client.get("/bins?limit=101").status_code, 422)
        self.assertEqual(self.client.get("/bins?offset=-1").status_code, 422)

    def test_missing_resources(self):
        responses = [
            self.client.get("/bins/999"),
            self.client.patch("/bins/999/fill", json={"fill_percent": 50}),
            self.client.post("/bins/999/collect"),
            self.client.post("/requests", json={"bin_id": 999}),
            self.client.post("/requests/999/cancel"),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404, response.text)

    def test_duplicate_pending_request_blocked(self):
        item = self.bin()
        self.request(item["id"])
        response = self.client.post("/requests", json={"bin_id": item["id"], "priority": "urgent"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.client.get("/requests?status=pending").json()), 1)

    def test_cancel_and_request_again(self):
        item = self.bin()
        request = self.request(item["id"])
        result = self.client.post(f"/requests/{request['id']}/cancel")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "cancelled")
        self.assertIsNotNone(result.json()["closed_at"])
        self.assertEqual(self.client.post(f"/requests/{request['id']}/cancel").status_code, 409)
        self.request(item["id"], "urgent")
        self.assertEqual(len(self.client.get("/requests?status=pending").json()), 1)
        self.assertEqual(len(self.client.get("/requests?status=cancelled").json()), 1)

    def test_collect_closes_request_and_resets_fill(self):
        item = self.bin(fill_percent=75)
        request = self.request(item["id"])
        response = self.client.post(f"/bins/{item['id']}/collect")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["estimated_litres"], 180)
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["fill_percent"], 0)
        closed = self.client.get("/requests?status=completed").json()[0]
        self.assertEqual(closed["id"], request["id"])
        self.assertIsNotNone(closed["closed_at"])
        self.assertEqual(self.client.post(f"/requests/{request['id']}/cancel").status_code, 409)
        self.assertEqual(len(self.client.get("/collections").json()), 1)

    def test_collect_without_request(self):
        item = self.bin()
        self.assertEqual(self.client.post(f"/bins/{item['id']}/collect").status_code, 201)
        self.assertEqual(self.client.get("/requests").json(), [])

    def test_repeated_collection_of_empty_bin_is_blocked(self):
        item = self.bin()
        self.client.post(f"/bins/{item['id']}/collect")
        self.assertEqual(self.client.post(f"/bins/{item['id']}/collect").status_code, 409)
        self.assertEqual(len(self.client.get("/collections").json()), 1)

    def test_empty_bin_with_request_can_be_closed(self):
        item = self.bin(fill_percent=0)
        self.request(item["id"])
        response = self.client.post(f"/bins/{item['id']}/collect")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["estimated_litres"], 0)

    def test_collection_rolls_back_all_changes_on_failure(self):
        item = self.bin()
        self.request(item["id"])
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("""CREATE TRIGGER reject_request_update
                BEFORE UPDATE ON pickup_requests BEGIN
                SELECT RAISE(ABORT, 'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(f"/bins/{item['id']}/collect")
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["fill_percent"], 90)
        self.assertEqual(self.client.get("/collections").json(), [])
        self.assertEqual(len(self.client.get("/requests?status=pending").json()), 1)

    def test_concurrent_collections_do_not_double_count(self):
        item = self.bin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(
                lambda _: self.client.post(f"/bins/{item['id']}/collect").status_code, range(2)
            ))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(len(self.client.get("/collections").json()), 1)

    def test_plan_priority_order_and_waste_separation(self):
        threshold = self.bin("Threshold", fill_percent=80)
        normal = self.bin("Normal", fill_percent=10, latitude=53.49)
        urgent = self.bin("Urgent", fill_percent=5, latitude=53.60)
        self.bin("Below threshold", fill_percent=79)
        self.bin("Recycling", waste_type="recycling", fill_percent=100)
        self.request(normal["id"])
        self.request(urgent["id"], "urgent")
        result = self.plan()
        self.assertEqual([stop["bin"]["id"] for stop in result["stops"]],
                         [urgent["id"], normal["id"], threshold["id"]])
        self.assertEqual([stop["reason"] for stop in result["stops"]],
                         ["urgent_request", "normal_request", "fill_threshold"])
        self.assertEqual(result["total_estimated_litres"], 228)

    def test_plan_nearest_neighbour_and_return_leg(self):
        farther = self.bin("Farther", latitude=0, longitude=2)
        nearer = self.bin("Nearer", latitude=0, longitude=1)
        result = self.plan(depot_latitude=0, depot_longitude=0)
        self.assertEqual([stop["bin"]["id"] for stop in result["stops"]],
                         [nearer["id"], farther["id"]])
        self.assertAlmostEqual(result["total_distance_km"], 444.780, places=2)
        self.assertAlmostEqual(result["return_to_depot_km"], 222.390, places=2)
        self.assertEqual(result["distance_method"], "haversine_straight_line")

    def test_plan_max_stops_and_read_only_behaviour(self):
        item = self.bin("A")
        self.bin("B")
        self.request(item["id"])
        before = self.client.get("/summary").json()
        self.assertEqual(len(self.plan(max_stops=1)["stops"]), 1)
        self.assertEqual(self.client.get("/summary").json(), before)
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["fill_percent"], 90)

    def test_plan_does_not_duplicate_requested_full_bin(self):
        item = self.bin()
        self.request(item["id"], "urgent")
        self.assertEqual(len(self.plan()["stops"]), 1)

    def test_cancelled_request_no_longer_qualifies_low_fill_bin(self):
        item = self.bin(fill_percent=20)
        request = self.request(item["id"])
        self.client.post(f"/requests/{request['id']}/cancel")
        self.assertEqual(self.plan()["stops"], [])

    def test_empty_plan(self):
        result = self.plan()
        self.assertEqual(result["stops"], [])
        self.assertEqual(result["total_distance_km"], 0)
        self.assertEqual(result["total_estimated_litres"], 0)

    def test_plan_validation(self):
        for invalid in [{"max_stops": 0}, {"fill_threshold": 101}, {"depot_latitude": 91}]:
            with self.subTest(invalid=invalid):
                payload = {"waste_type": "general", "depot_latitude": 0, "depot_longitude": 0}
                self.assertEqual(self.client.post("/plans", json=payload | invalid).status_code, 422)

    def test_summary(self):
        item = self.bin("A", fill_percent=50)
        other = self.bin("B", fill_percent=80)
        self.request(other["id"])
        self.client.post(f"/bins/{item['id']}/collect")
        self.assertEqual(self.client.get("/summary").json(), {
            "total_bins": 2, "bins_at_or_above_80_percent": 1, "pending_requests": 1,
            "completed_collections": 1, "estimated_litres_collected": 120,
        })

    def test_data_persists_across_app_instances(self):
        item = self.bin()
        with TestClient(create_app(self.db_path)) as second_client:
            self.assertEqual(second_client.get(f"/bins/{item['id']}").json()["label"], "Central")

    def test_sql_like_label_is_treated_as_data(self):
        label = "'); DROP TABLE bins; --"
        item = self.bin(label)
        self.assertEqual(self.client.get(f"/bins/{item['id']}").json()["label"], label)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_distance_known_cases(self):
        self.assertEqual(distance_km((0, 0), (0, 0)), 0)
        self.assertAlmostEqual(distance_km((0, 0), (0, 1)), 111.195, places=3)
        self.assertAlmostEqual(distance_km((0, 179.5), (0, -179.5)), 111.195, places=3)
        self.assertAlmostEqual(distance_km((0, 0), (0, 180)), 20015.114, places=3)


if __name__ == "__main__":
    unittest.main()

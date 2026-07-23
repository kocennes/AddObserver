"""Automated evidence for the five required mock incident drills."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.operations.drills import DRILLS, REQUIRED_STAGES, run_drill


class OperationsDrillTests(unittest.TestCase):
    def test_all_required_scenarios_complete_every_response_stage_without_secrets(self) -> None:
        self.assertEqual(
            set(DRILLS),
            {
                "credential_leak",
                "unauthorized_mutate",
                "audit_outage",
                "google_quota",
                "database_restore",
            },
        )
        for scenario in DRILLS:
            with self.subTest(scenario=scenario):
                result = run_drill(scenario)
                self.assertEqual(result.completed_stages, REQUIRED_STAGES)
                self.assertEqual(result.elapsed_minutes, 30)
                self.assertFalse(result.secret_used)

    def test_unknown_scenario_and_unbounded_timing_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            run_drill("real-secret")
        with self.assertRaises(ValueError):
            run_drill("audit_outage", minutes_per_stage=0)


if __name__ == "__main__":
    unittest.main()

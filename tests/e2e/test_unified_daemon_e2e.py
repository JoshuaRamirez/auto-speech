"""Unified E2E Test Suite for auto-speech Unified Daemon Server (Tiers 1-4).

Aggregates all end-to-end tests across the 4 tiers:
- Tier 1: Feature Coverage (R1, R2, R3)
- Tier 2: Boundary & Corner Cases (R1, R2, R3)
- Tier 3: Cross-Feature Interactions
- Tier 4: Real-World Scenarios
"""

from __future__ import annotations

import unittest

from tests.e2e.test_tier1_features import (
    TestTier1R1InProcessAudioSink,
    TestTier1R2ThinClientIPC,
    TestTier1R3DeadSprawlRemoval,
)
from tests.e2e.test_tier2_boundaries import (
    TestTier2R1Boundaries,
    TestTier2R2Boundaries,
    TestTier2R3Boundaries,
)
from tests.e2e.test_tier3_combinations import (
    TestTier3CrossFeatureCombinations,
)
from tests.e2e.test_tier4_scenarios import (
    TestTier4RealWorldScenarios,
)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for test_class in [
        TestTier1R1InProcessAudioSink,
        TestTier1R2ThinClientIPC,
        TestTier1R3DeadSprawlRemoval,
        TestTier2R1Boundaries,
        TestTier2R2Boundaries,
        TestTier2R3Boundaries,
        TestTier3CrossFeatureCombinations,
        TestTier4RealWorldScenarios,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(test_class))
    return suite


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""E2E Test Runner for auto-speech Unified Daemon Server.

Usage:
    .venv/bin/python tests/e2e/run_e2e.py [--tier {1,2,3,4,5,all}] [-v]
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.e2e.test_tier1_features import (  # noqa: E402
    TestTier1R1InProcessAudioSink,
    TestTier1R2ThinClientIPC,
    TestTier1R3DeadSprawlRemoval,
)
from tests.e2e.test_tier2_boundaries import (  # noqa: E402
    TestTier2R1Boundaries,
    TestTier2R2Boundaries,
    TestTier2R3Boundaries,
)
from tests.e2e.test_tier3_combinations import (  # noqa: E402
    TestTier3CrossFeatureCombinations,
)
from tests.e2e.test_tier4_scenarios import (  # noqa: E402
    TestTier4RealWorldScenarios,
)
from tests.e2e.test_tier5_adversarial_sink_ipc import (  # noqa: E402
    TestTier5AdversarialAudioSink,
    TestTier5AdversarialSocketIPC,
    TestTier5AdversarialIntegratedWorkflow,
)
from tests.e2e.test_tier5_adversarial_lifecycle import (  # noqa: E402
    TestTier5AdversarialSocketLifecycle,
    TestTier5AdversarialQueueBackpressureFlood,
    TestTier5AdversarialSignalAndInterruption,
    TestTier5AdversarialWorkerAndHandlerRobustness,
)

TIER_MAP = {
    1: [
        TestTier1R1InProcessAudioSink,
        TestTier1R2ThinClientIPC,
        TestTier1R3DeadSprawlRemoval,
    ],
    2: [
        TestTier2R1Boundaries,
        TestTier2R2Boundaries,
        TestTier2R3Boundaries,
    ],
    3: [
        TestTier3CrossFeatureCombinations,
    ],
    4: [
        TestTier4RealWorldScenarios,
    ],
    5: [
        TestTier5AdversarialAudioSink,
        TestTier5AdversarialSocketIPC,
        TestTier5AdversarialIntegratedWorkflow,
        TestTier5AdversarialSocketLifecycle,
        TestTier5AdversarialQueueBackpressureFlood,
        TestTier5AdversarialSignalAndInterruption,
        TestTier5AdversarialWorkerAndHandlerRobustness,
    ],
}


def build_suite(tier: str | int = "all") -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    if str(tier).lower() == "all":
        selected_tiers = [1, 2, 3, 4, 5]
    else:
        selected_tiers = [int(tier)]

    for t in selected_tiers:
        for test_case in TIER_MAP[t]:
            suite.addTests(loader.loadTestsFromTestCase(test_case))

    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="auto-speech E2E Test Suite Runner")
    parser.add_argument(
        "--tier",
        type=str,
        default="all",
        choices=["1", "2", "3", "4", "5", "all"],
        help="Run tests for a specific tier (1, 2, 3, 4, 5, or all)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose test execution output",
    )
    args = parser.parse_args(argv)

    suite = build_suite(args.tier)
    verbosity = 2 if args.verbose else 1

    print("=" * 70)
    print(" auto-speech Unified Daemon Server — E2E Test Suite")
    print(f" Target Tier: {args.tier.upper()}")
    print(f" Total Tests Selected: {suite.countTestCases()}")
    print("=" * 70)

    t0 = time.time()
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    elapsed = time.time() - t0

    print("\n" + "=" * 70)
    print(f" Summary: Ran {result.testsRun} tests in {elapsed:.2f}s")
    print(f" Passed:   {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f" Failed:   {len(result.failures)}")
    print(f" Errors:   {len(result.errors)}")
    print("=" * 70)

    if result.failures or result.errors:
        print("\nNote: Failures correspond to features pending implementation milestones:")
        print("  - R1 NativeAudioSink / In-Process TTSEngine: Milestone M1")
        print("  - R2 Thin Client speak.py / Daemon UNIX Socket: Milestone M2")
        print("  - R3 Dead Architectural Sprawl Deletion: Milestone M3")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from performance import (GpuStatus, SystemResources,
                         resolve_performance_budget)


def resources(cores=16, total=32768, available=20000, directml=True):
    return SystemResources(
        logical_cores=cores,
        total_memory_mb=total,
        available_memory_mb=available,
        gpu=GpuStatus(name="Test GPU", memory_mb=6144,
                      directml_available=directml,
                      cpu_provider_available=True),
    )


class PerformanceBudgetTests(unittest.TestCase):
    def test_four_modes_have_increasing_or_custom_budgets(self):
        eco = resolve_performance_budget(
            {"performance_mode": "eco"}, resources())
        balanced = resolve_performance_budget(
            {"performance_mode": "balanced"}, resources())
        high = resolve_performance_budget(
            {"performance_mode": "high"}, resources())
        custom = resolve_performance_budget({
            "performance_mode": "custom",
            "custom_cpu_workers": 4,
            "custom_ai_workers": 5,
            "custom_active_documents": 6,
            "memory_limit_percent": 82,
            "ocr_dpi": 240,
        }, resources())

        self.assertEqual(eco.cpu_workers, 1)
        self.assertLessEqual(eco.ai_workers, balanced.ai_workers)
        self.assertLessEqual(balanced.cpu_workers, high.cpu_workers)
        self.assertEqual(custom.cpu_workers, 4)
        self.assertEqual(custom.ai_workers, 5)
        self.assertEqual(custom.ocr_dpi, 240)

    def test_low_memory_and_cpu_are_safely_clamped(self):
        budget = resolve_performance_budget(
            {"performance_mode": "high"},
            resources(cores=2, total=4096, available=700))
        self.assertEqual(budget.cpu_workers, 1)
        self.assertGreaterEqual(budget.active_documents, 1)
        self.assertLessEqual(budget.ai_workers, budget.active_documents)

    def test_gpu_auto_requires_directml_and_can_be_disabled(self):
        ready = resolve_performance_budget(
            {"gpu_mode": "auto", "gpu_ocr_enabled": True}, resources())
        unavailable = resolve_performance_budget(
            {"gpu_mode": "auto", "gpu_ocr_enabled": True},
            resources(directml=False))
        disabled = resolve_performance_budget(
            {"gpu_mode": "off", "gpu_ocr_enabled": True}, resources())
        self.assertTrue(ready.gpu_ocr_ready)
        self.assertFalse(unavailable.gpu_ocr_ready)
        self.assertFalse(disabled.gpu_ocr_ready)

    def test_invalid_old_settings_fall_back_to_balanced(self):
        budget = resolve_performance_budget(
            {"performance_mode": "unknown", "gpu_mode": "bad"},
            resources())
        self.assertEqual(budget.mode, "balanced")
        self.assertEqual(budget.gpu_mode, "auto")


if __name__ == "__main__":
    unittest.main()

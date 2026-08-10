import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gpu_acceleration import extract_result_text, run_ocr


class Output:
    txts = ["First line", "Second line"]


class GpuAccelerationTests(unittest.TestCase):
    def test_parses_v3_and_legacy_results(self):
        self.assertEqual(
            extract_result_text(Output()), ["First line", "Second line"])
        legacy = [([0, 0], "Legacy", 0.99)]
        self.assertEqual(extract_result_text(legacy), ["Legacy"])
        self.assertEqual(
            extract_result_text((legacy, 0.12)), ["Legacy"])

    def test_gpu_failure_falls_back_to_cpu(self):
        gpu = lambda image: (_ for _ in ()).throw(RuntimeError("DML error"))
        cpu = lambda image: Output()
        logs = []
        with patch("gpu_acceleration.get_ocr_engine",
                   side_effect=[gpu, cpu]):
            lines, provider = run_ocr(b"image", True, logs.append)
        self.assertEqual(provider, "cpu")
        self.assertEqual(lines, ["First line", "Second line"])
        self.assertIn("自动切换 CPU", logs[0])

    def test_cpu_mode_does_not_initialize_gpu(self):
        with patch("gpu_acceleration.get_ocr_engine",
                   return_value=lambda image: Output()) as mocked:
            _, provider = run_ocr(b"image", False)
        self.assertEqual(provider, "cpu")
        mocked.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()

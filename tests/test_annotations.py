from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from issue_delivery_orchestrator.annotations import annotate_png, normalize_callouts
from issue_delivery_orchestrator.errors import OrchestrationError
from issue_delivery_orchestrator.png_codec import PngImage, decode_png, encode_png


class AnnotationTests(unittest.TestCase):
    def test_draws_highlight_without_modifying_original(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.png"
            target = root / "annotated.png"
            original = encode_png(
                PngImage(120, 80, bytearray((255, 255, 255, 255) * 120 * 80))
            )
            source.write_bytes(original)
            callouts = normalize_callouts(
                [
                    {
                        "kind": "highlight",
                        "caption": "Nuevo control",
                        "bounds": {
                            "x": 0.25,
                            "y": 0.25,
                            "width": 0.5,
                            "height": 0.5,
                        },
                    }
                ],
                1,
            )

            annotate_png(source, target, callouts)

            result = decode_png(target.read_bytes())
            center = (40 * result.width + 60) * 4
            untouched = (75 * result.width + 115) * 4
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((result.width, result.height), (120, 80))
            self.assertNotEqual(result.pixels[center : center + 4], b"\xff\xff\xff\xff")
            self.assertEqual(result.pixels[untouched : untouched + 4], b"\xff\xff\xff\xff")

    def test_rejects_out_of_bounds_callout(self):
        with self.assertRaisesRegex(OrchestrationError, "must fit"):
            normalize_callouts(
                [
                    {
                        "kind": "circle",
                        "caption": "Outside",
                        "bounds": {
                            "x": 0.8,
                            "y": 0.2,
                            "width": 0.3,
                            "height": 0.2,
                        },
                    }
                ],
                1,
            )

    def test_arrow_requires_anchor(self):
        with self.assertRaisesRegex(OrchestrationError, "missing anchor"):
            normalize_callouts(
                [
                    {
                        "kind": "arrow",
                        "caption": "Target",
                        "bounds": {
                            "x": 0.2,
                            "y": 0.2,
                            "width": 0.3,
                            "height": 0.2,
                        },
                    }
                ],
                1,
            )

    def test_rejects_callout_that_covers_most_of_image(self):
        with self.assertRaisesRegex(OrchestrationError, "covers too much"):
            normalize_callouts(
                [
                    {
                        "kind": "highlight",
                        "caption": "Global",
                        "bounds": {
                            "x": 0.05,
                            "y": 0.05,
                            "width": 0.9,
                            "height": 0.9,
                        },
                    }
                ],
                1,
            )

    def test_normalizes_mislabeled_jpeg_without_touching_source(self):
        sips = Path("/usr/bin/sips")
        if not sips.is_file():
            self.skipTest("macOS sips is unavailable")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            png = root / "input.png"
            source = root / "captured-as-png.png"
            target = root / "annotated.png"
            png.write_bytes(
                encode_png(
                    PngImage(80, 50, bytearray((250, 250, 250, 255) * 4000))
                )
            )
            subprocess.run(
                [str(sips), "-s", "format", "jpeg", str(png), "--out", str(source)],
                check=True,
                capture_output=True,
            )
            original = source.read_bytes()
            callouts = normalize_callouts(
                [
                    {
                        "kind": "circle",
                        "caption": "Target",
                        "bounds": {
                            "x": 0.2,
                            "y": 0.2,
                            "width": 0.4,
                            "height": 0.4,
                        },
                    }
                ],
                1,
            )

            annotate_png(source, target, callouts)

            self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))
            self.assertEqual(source.read_bytes(), original)


class ValidateScreenshotTests(unittest.TestCase):
    def test_validate_screenshot_rejects_empty_unicode_named_file(self):
        from issue_delivery_orchestrator.annotations import validate_screenshot
        from issue_delivery_orchestrator.errors import OrchestrationError

        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "空のスクリーンショット.png"
            source.write_bytes(b"")

            with self.assertRaisesRegex(OrchestrationError, "Invalid screenshot"):
                validate_screenshot(source)


if __name__ == "__main__":
    unittest.main()

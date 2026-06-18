import unittest

from clothing_change_detector import (
    ClothingChangeOptions,
    ClothingSample,
    PersonBox,
    clothing_crop_from_person_box,
    histogram_distance,
    hsv_histogram_from_rgb_bytes,
    sample_clothing_histograms,
    select_confirmed_change_points,
)


class ClothingChangeDetectorTests(unittest.TestCase):
    def test_hsv_histogram_separates_different_clothing_colors(self):
        red = bytes([220, 20, 20]) * 16
        blue = bytes([20, 20, 220]) * 16

        red_histogram = hsv_histogram_from_rgb_bytes(red, width=4, height=4)
        blue_histogram = hsv_histogram_from_rgb_bytes(blue, width=4, height=4)

        self.assertGreater(histogram_distance(red_histogram, blue_histogram), 0.8)
        self.assertLess(histogram_distance(red_histogram, red_histogram), 0.01)

    def test_histogram_uses_clothing_crop_region(self):
        # Full frame background is red, clothing crop in the center is blue.
        pixels = []
        for y in range(4):
            for x in range(4):
                pixels.extend([20, 20, 220] if 1 <= x <= 2 and 1 <= y <= 2 else [220, 20, 20])
        histogram = hsv_histogram_from_rgb_bytes(
            bytes(pixels),
            width=4,
            height=4,
            crop=(0.25, 0.25, 0.5, 0.5),
        )
        blue_histogram = hsv_histogram_from_rgb_bytes(bytes([20, 20, 220]) * 4, width=2, height=2)

        self.assertLess(histogram_distance(histogram, blue_histogram), 0.01)

    def test_select_confirmed_change_points_requires_consecutive_changed_samples(self):
        red = [1.0, 0.0, 0.0]
        blue = [0.0, 1.0, 0.0]
        samples = [
            ClothingSample(0.0, red),
            ClothingSample(0.33, blue),
            ClothingSample(0.66, red),
            ClothingSample(1.0, blue),
            ClothingSample(1.33, blue),
            ClothingSample(1.66, blue),
        ]

        cut_points = select_confirmed_change_points(
            samples,
            ClothingChangeOptions(threshold=0.5, confirmation_frames=2, min_change_gap_sec=0.8),
        )

        self.assertEqual(cut_points, [1.0])

    def test_adjacent_mode_detects_repeated_outfit_changes(self):
        red = [1.0, 0.0, 0.0]
        blue = [0.0, 1.0, 0.0]
        green = [0.0, 0.0, 1.0]
        samples = [
            ClothingSample(0.0, red),
            ClothingSample(0.4, blue),
            ClothingSample(0.8, blue),
            ClothingSample(1.2, green),
            ClothingSample(1.6, green),
            ClothingSample(2.0, red),
        ]

        cut_points = select_confirmed_change_points(
            samples,
            ClothingChangeOptions(
                threshold=0.5,
                confirmation_frames=1,
                min_change_gap_sec=0.8,
                comparison_mode="adjacent",
            ),
        )

        self.assertEqual(cut_points, [0.4, 1.2, 2.0])

    def test_clothing_crop_is_derived_from_person_box(self):
        crop = clothing_crop_from_person_box(
            PersonBox(x1=20, y1=10, x2=80, y2=90, confidence=0.9),
            width=100,
            height=100,
            torso_crop=(0.25, 0.25, 0.5, 0.5),
        )

        self.assertEqual(crop, (0.35, 0.3, 0.3, 0.4))

    def test_sample_histograms_use_person_detector_bbox_when_available(self):
        # The frame is red except for a blue 2x2 person/clothing box in the center.
        pixels = []
        for y in range(4):
            for x in range(4):
                pixels.extend([20, 20, 220] if 1 <= x <= 2 and 1 <= y <= 2 else [220, 20, 20])

        def fake_runner(command, **kwargs):
            if command[0] == "ffprobe":
                return type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": (
                            '{"streams":[{"codec_type":"video","width":4,"height":4,'
                            '"avg_frame_rate":"1/1"}],"format":{"duration":"1.0"}}'
                        ),
                        "stderr": "",
                    },
                )()
            return type("Result", (), {"returncode": 0, "stdout": bytes(pixels), "stderr": b""})()

        def fake_detector(rgb, width, height):
            return PersonBox(1, 1, 3, 3, 0.99)

        samples = sample_clothing_histograms(
            "input.mp4",
            ClothingChangeOptions(sample_fps=1, analysis_width=4, person_torso_crop=(0.0, 0.0, 1.0, 1.0)),
            runner=fake_runner,
            person_detector=fake_detector,
        )
        blue_histogram = hsv_histogram_from_rgb_bytes(bytes([20, 20, 220]) * 4, width=2, height=2)

        self.assertLess(histogram_distance(samples[0].histogram, blue_histogram), 0.01)


if __name__ == "__main__":
    unittest.main()

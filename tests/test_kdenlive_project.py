import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_project import KdenliveProjectBuilder, validate_project_xml
from kdenlive_timeline import TimelineClip, TimelineEffect, TimelineProject, TimelineTrack


class KdenliveProjectTests(unittest.TestCase):
    def test_builder_writes_profile_producer_playlist_and_tractor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "black.png"
            image.write_bytes(b"png")
            project = TimelineProject(width=1080, height=1920, fps=30)
            project.tracks.append(
                TimelineTrack(
                    id="video-main",
                    kind="video",
                    role="video",
                    clips=[
                        TimelineClip(
                            id="clip-1",
                            source=image,
                            start_frame=0,
                            duration_frames=90,
                        )
                    ],
                )
            )
            output = root / "demo.kdenlive"

            KdenliveProjectBuilder().write(project, output)
            tree = ET.parse(output)

        self.assertEqual(tree.getroot().tag, "mlt")
        self.assertIsNotNone(tree.find(".//profile"))
        self.assertIsNotNone(tree.find(".//producer"))
        self.assertIsNotNone(tree.find(".//playlist"))
        self.assertIsNotNone(tree.find(".//tractor"))

    def test_validator_rejects_missing_producer_reference(self):
        xml = b'<mlt><playlist id="p"><entry producer="missing" in="0" out="29"/></playlist></mlt>'

        with self.assertRaises(ValueError):
            validate_project_xml(xml)

    def test_builder_inserts_blank_for_timeline_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "black.png"
            image.write_bytes(b"png")
            project = TimelineProject(width=1080, height=1920, fps=30)
            project.tracks.append(
                TimelineTrack(
                    id="video-main",
                    kind="video",
                    role="video",
                    clips=[
                        TimelineClip("clip-1", image, 30, 30),
                    ],
                )
            )
            output = root / "demo.kdenlive"

            KdenliveProjectBuilder().write(project, output)
            tree = ET.parse(output)

        blank = tree.find(".//playlist/blank")
        self.assertIsNotNone(blank)
        self.assertEqual(blank.attrib["length"], "30")

    def test_builder_serializes_markers_and_clip_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "ring.png"
            image.write_bytes(b"png")
            project = TimelineProject(width=1080, height=1920, fps=30, markers=[0, 30])
            project.tracks.append(
                TimelineTrack(
                    id="stickers",
                    kind="video",
                    role="sticker",
                    clips=[
                        TimelineClip(
                            "sticker-1",
                            image,
                            0,
                            30,
                            name="旋彩光圈",
                            role="sticker",
                            effects=[
                                TimelineEffect(
                                    service="qtblend",
                                    source_name="transform",
                                    status="approximated",
                                    properties={"rect": "57.0% 39.5% 35% 35% 1"},
                                )
                            ],
                        )
                    ],
                )
            )
            output = root / "demo.kdenlive"

            KdenliveProjectBuilder().write(project, output)
            tree = ET.parse(output)

        marker = tree.find(".//property[@name='kdenlive:guide.0']")
        effect = tree.find(".//entry/filter")
        self.assertIsNotNone(marker)
        self.assertIsNotNone(effect)
        self.assertEqual(effect.find("./property[@name='mlt_service']").text, "qtblend")
        self.assertEqual(effect.find("./property[@name='kdenlive:effect.status']").text, "approximated")

    def test_builder_serializes_nested_sequences(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "clip.png"
            image.write_bytes(b"png")
            child = TimelineProject(width=1080, height=1920, fps=30)
            child.tracks.append(
                TimelineTrack("sequence-1-video", "video", "video", [TimelineClip("child-clip", image, 0, 30)])
            )
            project = TimelineProject(width=1080, height=1920, fps=30, sequences={"sequence-1": child})
            project.tracks.append(
                TimelineTrack(
                    "video-main",
                    "video",
                    "video",
                    [TimelineClip("compound-1", None, 0, 30, nested_sequence_id="sequence-1")],
                )
            )
            output = root / "demo.kdenlive"

            KdenliveProjectBuilder().write(project, output)
            tree = ET.parse(output)

        tractors = {tractor.attrib["id"] for tractor in tree.findall(".//tractor")}
        nested_entry = tree.find(".//playlist[@id='video-main']/entry")
        self.assertIn("sequence-1", tractors)
        self.assertEqual(nested_entry.attrib["producer"], "sequence-1")


if __name__ == "__main__":
    unittest.main()

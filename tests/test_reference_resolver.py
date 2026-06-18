import tempfile
import unittest
from pathlib import Path

from reference_resolver import MissingReferenceError, ReferenceResolver


class ReferenceResolverTests(unittest.TestCase):
    def test_scene_specific_references_win_over_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")
            (root / "default_background.png").write_bytes(b"default background")
            (root / "scene_002_person.jpg").write_bytes(b"scene person")
            (root / "scene_002_background.webp").write_bytes(b"scene background")

            resolved = ReferenceResolver(root).resolve(2)

        self.assertEqual(resolved.scene_index, 2)
        self.assertEqual(resolved.person.name, "scene_002_person.jpg")
        self.assertEqual(resolved.background.name, "scene_002_background.webp")

    def test_defaults_are_used_when_scene_specific_files_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")
            (root / "default_background.png").write_bytes(b"default background")

            resolved = ReferenceResolver(root).resolve(5)

        self.assertEqual(resolved.person.name, "default_person.png")
        self.assertEqual(resolved.background.name, "default_background.png")

    def test_missing_reference_names_the_missing_scene_and_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")

            with self.assertRaises(MissingReferenceError) as raised:
                ReferenceResolver(root).resolve(3)

        self.assertIn("scene_003", str(raised.exception))
        self.assertIn("background", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

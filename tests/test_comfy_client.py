import unittest
from pathlib import Path

from comfy_client import ComfyClient, WorkflowBindingError, find_video_outputs, patch_workflow


class ComfyClientTests(unittest.TestCase):
    def test_patch_workflow_updates_bound_inputs_only(self):
        workflow = {
            "10": {"inputs": {"video": "old.mp4", "keep": "same"}},
            "11": {"inputs": {"image": "old-person.png"}},
            "12": {"inputs": {"image": "old-bg.png"}},
            "13": {"inputs": {"filename_prefix": "old"}},
        }
        bindings = {
            "video_path": {"node": "10", "field": "video"},
            "person_image": {"node": "11", "field": "image"},
            "background_image": {"node": "12", "field": "image"},
            "output_prefix": {"node": "13", "field": "filename_prefix"},
        }

        patched = patch_workflow(
            workflow,
            bindings,
            video_path=Path("scene.mp4"),
            person_image=Path("person.png"),
            background_image=Path("background.png"),
            output_prefix="scene_001",
        )

        self.assertEqual(patched["10"]["inputs"]["video"], "scene.mp4")
        self.assertEqual(patched["10"]["inputs"]["keep"], "same")
        self.assertEqual(patched["11"]["inputs"]["image"], "person.png")
        self.assertEqual(patched["12"]["inputs"]["image"], "background.png")
        self.assertEqual(patched["13"]["inputs"]["filename_prefix"], "scene_001")

    def test_missing_binding_raises_named_error(self):
        with self.assertRaises(WorkflowBindingError) as raised:
            patch_workflow(
                {},
                {},
                video_path=Path("scene.mp4"),
                person_image=Path("p.png"),
                background_image=Path("b.png"),
                output_prefix="x",
            )

        self.assertIn("video_path", str(raised.exception))

    def test_submit_and_poll_success(self):
        calls = []

        def fake_request(method, url, payload=None, timeout=30):
            calls.append((method, url, payload))
            if url.endswith("/prompt"):
                return {"prompt_id": "abc"}
            if url.endswith("/history/abc"):
                return {"abc": {"outputs": {"1": {"gifs": [{"filename": "scene_001.mp4", "subfolder": "", "type": "output"}]}}}}
            raise AssertionError(url)

        client = ComfyClient("http://127.0.0.1:8188", request_json=fake_request, sleep=lambda _: None)
        result = client.submit_and_wait({"1": {"inputs": {}}}, timeout_sec=3, poll_interval=0.01)

        self.assertEqual(result.prompt_id, "abc")
        self.assertEqual(result.history["abc"]["outputs"]["1"]["gifs"][0]["filename"], "scene_001.mp4")

    def test_find_video_outputs_reads_gifs_and_videos(self):
        history = {
            "abc": {
                "outputs": {
                    "1": {
                        "gifs": [{"filename": "scene_001.mp4"}],
                        "videos": [{"filename": "preview.webm"}],
                        "images": [{"filename": "still.png"}],
                    }
                }
            }
        }

        self.assertEqual(find_video_outputs(history), ["scene_001.mp4", "preview.webm"])


if __name__ == "__main__":
    unittest.main()

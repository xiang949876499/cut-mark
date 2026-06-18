from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]


class MissingReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneReferences:
    scene_index: int
    person: Path
    background: Path


class ReferenceResolver:
    def __init__(self, refs_dir: Path) -> None:
        self.refs_dir = Path(refs_dir)

    def resolve(self, scene_index: int) -> SceneReferences:
        label = f"scene_{scene_index:03d}"
        person = self._find_first([f"{label}_person", "default_person"], label, "person")
        background = self._find_first([f"{label}_background", "default_background"], label, "background")
        return SceneReferences(scene_index=scene_index, person=person, background=background)

    def _find_first(self, stems: list[str], label: str, kind: str) -> Path:
        for stem in stems:
            for suffix in IMAGE_EXTENSIONS:
                candidate = self.refs_dir / f"{stem}{suffix}"
                if candidate.is_file():
                    return candidate
        raise MissingReferenceError(f"Missing {kind} reference for {label}")

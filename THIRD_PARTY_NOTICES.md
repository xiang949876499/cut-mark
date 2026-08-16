# Third-Party Notices

Last reviewed: 2026-08-16.

The repository owner's original contributions are licensed under
[GNU AGPL-3.0](./LICENSE); the applicable copyright notice is in
[NOTICE](./NOTICE). Third-party software, models, media, data, and services
remain governed by their own terms. This inventory is not legal advice.

## Ultralytics YOLO

- Integration: clothing_change_detector.py directly imports ultralytics.YOLO;
  the outfit-change workflow may use it for person-region detection.
- Upstream: [Ultralytics](https://github.com/ultralytics/ultralytics).
- Terms: [Ultralytics licensing](https://www.ultralytics.com/license).
- Compatibility path: this repository follows the AGPL-3.0 path. For a
  modified or combined work that is distributed or offered for remote network
  interaction, comply with the applicable AGPL-3.0 source, notice, and
  corresponding-source obligations. A remote deployment must offer its users
  a no-charge way to obtain the applicable Corresponding Source.
- Alternative: a commercial or proprietary deployment that does not meet the
  AGPL-3.0 conditions requires an Ultralytics Enterprise license.

## Optional tools

- pyJianYingDraft is declared in requirements.txt; verify its Apache-2.0
  notice and any bundled-material obligations for the exact released version.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) is declared in requirements.txt
  and is released under the Unlicense.
- All input video, audio, images, website content, models, and ComfyUI
  workflows remain subject to their own rights and service terms.

This file is an engineering inventory, not legal advice.

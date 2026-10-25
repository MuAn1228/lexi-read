# Lexi character assets

The current local preview uses the user-requested original `assets/DSniang1.png`
from [DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget),
pinned to commit `5247dd6813d6e4430728f39ccc63d43cda046022`.

It is stored in **`frontend/public/local-mascot/`**, which is excluded from Git.
The local production build copies it into the ignored `frontend/dist/` directory.
It is not included as a redistributable asset in this source repository.
SHA-256: `dcc456613dbb1102a4b7ef636a8b622371e33b763489d6249c434fb54248ba5c`.

Upstream's [PROVENANCE](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget/blob/5247dd6813d6e4430728f39ccc63d43cda046022/PROVENANCE.md)
excludes artwork from MIT and grants no cross-project redistribution license.
The upstream image is supplied unchanged for this user's local preview, with no
claim that Lexi created it or owns it. Replace it or obtain permission before
publishing a build containing the portrait.

`idle.png` is the independent, hand-authored 64 × 72 transparent PNG fallback.
`idle.pixels.txt` is its editable palette grid; palette and drawing commands are
in `scripts/generate_mascot_asset.py` (standard Python only).
The fallback uses indigo short hair, pink eyes, a modest white reading jacket
and an open green book. No upstream pixels are used in this fallback.

The built-in image-generation service returned HTTP 404. The user explicitly
chose hand-authored pixels, then requested the upstream character instead.
No external image-generation API or paid model was used.

`MascotAssets` supports optional thinking/happy/error images, a safe fallback,
and pixelated rendering. The upstream portrait keeps its original smooth style;
the fallback uses crisp pixel rendering. No GIF, sound or Live2D dependency is
loaded.

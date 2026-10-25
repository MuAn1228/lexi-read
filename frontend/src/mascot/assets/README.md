# Lexi character assets

## Current study character (2026-10-26)

`lexi-study-atlas-v2.png` is the default artwork, generated with the built-in
`image_gen.imagegen` tool from the character sheet supplied by the user. It
retains the reference's blue hair, white frilled headband, ribbon and chibi
silhouette, with book/notebook props for learning. This is an AI interpretation
of the user's reference; it is not the unchanged portrait from the old project.

The PNG is a 1536×1024 RGBA sheet, four columns by two rows. Its eight static
poses are idle, reading, thinking, explaining, searching, success, uncertain and
error. It has a real transparent alpha channel; no background removal or raster
painting was applied after generation. The generation prompt is retained in
`lexi-study-atlas-v2.prompt.txt`, and hashes/dimensions are in the adjacent JSON.

Each pose uses a 384×512 viewport. Two measured source corrections prevent a
neighboring lock of hair appearing in thinking, and retain all explaining hair:
thinking clips 12 source pixels at right; explaining shifts source X by −12.
The renderer keeps the original 3:4 aspect ratio, leaves 24px below the artwork
for the assistant buttons and never changes the widget's fixed geometry.

Gestures use these new static expressions with existing finite CSS movements.
There are no new walking/running frame loops, sounds or automatic AI requests.
Reduced motion still swaps static expressions without animation. A missing sheet
falls back once to the existing independent `idle.png`. The default renderer no
longer requests `DSniang1.png` or `rua.gif`.

## Previous local preview, retained as history

The earlier local preview used the user-requested original `assets/DSniang1.png`
from [DeepSeek-Balance-Whale-Widget](https://github.com/MeteorNOX/DeepSeek-Balance-Whale-Widget),
pinned to commit `5247dd6813d6e4430728f39ccc63d43cda046022`.

It is stored in **`frontend/public/local-mascot/`**, which is excluded from Git.
The local production build copies it into the ignored `frontend/dist/` directory.
It is not included as a redistributable asset in this source repository.
SHA-256: `dcc456613dbb1102a4b7ef636a8b622371e33b763489d6249c434fb54248ba5c`.

The optional original `assets/rua.gif` from the same pinned commit supplies the
finite pet gesture, unchanged. It is also stored only in the ignored local
directory, under the same artwork restrictions. SHA-256:
`0b9dd76db4ea86ce3d3b07a56054ad33f25f6eb7a320472a4810859f183fc332`.
The GIF has 10 frames at 128 × 128, one 200ms cycle; the widget removes it after
about one second. It is never loaded when reduced motion is enabled. Missing
GIFs fall back to the portrait and are not retried for each action.

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

During the original V1 attempt, the built-in image-generation service returned HTTP 404. The user explicitly
chose hand-authored pixels, then requested the upstream character instead.
No external image-generation API or paid model was used.

`MascotAssets` now supports a state atlas as well as optional thinking/happy/error/pet images, a safe fallback,
and pixelated rendering. The upstream portrait keeps its original smooth style;
the fallback uses crisp pixel rendering. Nod, tilt, hop and stretch use finite
CSS body motions; expressions are separate icon stickers, not edited facial
frames. No sound or Live2D dependency is loaded.

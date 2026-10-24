# Optional KittenTTS Micro pronunciation

KittenTTS Micro 0.8 is an optional local synthesized voice. It is separate from
dictionary recordings and is not a recording of a human saying the requested
word. The upstream phonemizer uses American English (`en-us`); the eight voice
names do not establish British English support or correct pronunciation for
every spelling.

| Component | Source | License |
| --- | --- | --- |
| KittenTTS Micro 0.8 model, voice embeddings and config | https://huggingface.co/KittenML/kitten-tts-micro-0.8 | Apache-2.0 |
| KittenTTS 0.8.1 reference inference and tokenization | https://github.com/KittenML/KittenTTS/tree/f0282f0198d497b7256535b755f9f3e339c1baa7 | Apache-2.0, per its LICENSE |
| ONNX Runtime | https://github.com/microsoft/onnxruntime | MIT |
| phonemizer | https://github.com/bootphon/phonemizer | GPL-3.0-or-later |
| eSpeak NG, distributed by espeakng-loader | https://github.com/espeak-ng/espeak-ng | GPL-3.0-or-later |
| espeakng-loader | https://github.com/thewh1teagle/espeakng-loader | MIT for the loader; bundled eSpeak NG retains its own license |

The full Apache license supplied by KittenML is retained in
[KITTEN-LICENSE.txt](KITTEN-LICENSE.txt). The upstream package's `setup.py`
contains an inconsistent MIT classifier; its actual LICENSE and model cards
specify Apache-2.0. Preserve the respective packages' license files and source
notices when distributing an installation containing them.

`scripts/setup_natural_tts.py` downloads only the following public assets, at
fixed model revision `1ccf72b2c2048fd17efac7de2fab32d10e225084`:

| File | Bytes | SHA256 |
| --- | ---: | --- |
| kitten_tts_micro_v0_8.onnx | 41,384,970 | `95481626fee1ba70ce683e69c534fc7cb38433c46ce42d3abbeafb4b9f1a4123` |
| voices.npz | 3,278,902 | `112710c1be8ad0e967c190fb0fd95cbe5848ec4791b93209f20b28b7da20dac1` |
| config.json | 473 | `1f0bd2208348f9211cb0da64fcd1536eb28228571cc6b09e767eb6e203a0a532` |

The default destination is the configured backend data directory's
`tts/kitten-micro-0.8` subdirectory. A separate directory can be selected with
`--data-dir`. The setup command checks size and SHA256 before atomically
replacing files, reuses matching files, and does not install runtime packages.
Voice assets are optional local data and are not committed to this repository.
Model setup sends no learning records, API keys or pronunciation text.

The reference runtime and text cleaner are separately pinned to upstream commit
`f0282f0198d497b7256535b755f9f3e339c1baa7`. Adaptations must retain this source
notice and identify changes. An integration can use an explicit CPU session,
load already verified local files, keep the complete generated waveform, and add
the application's existing playback buffering. These changes do not imply that
the synthesized output has been verified as a dictionary pronunciation.

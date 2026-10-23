# Optional local English pronunciation

The local pronunciation feature uses these separately installed components:

| Component | Source | License |
| --- | --- | --- |
| piper-tts 1.8.0 | https://github.com/OHF-Voice/piper1-gpl | GPL-3.0-or-later |
| LJSpeech medium / Cori medium voices | https://brycebeattie.com/files/tts/ | Public domain, per the model author |
| ONNX Runtime | https://github.com/microsoft/onnxruntime | MIT |
| eSpeak NG, included in Piper | https://github.com/espeak-ng/espeak-ng | GPL-3.0-or-later |

`scripts/setup_tts.py` downloads each ONNX model and its JSON configuration from
the published Piper voices repository, at fixed revision
`5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`:
https://huggingface.co/rhasspy/piper-voices
Model SHA256 digests match the publisher's LFS object IDs. Configuration files
are pinned by SHA256 as well. These binary files are optional local data, not
part of this repository. Package distributions retain their own license files.
Preserve applicable licenses and notices when distributing an installation
containing these components.

The American model is `en_US-ljspeech-medium`; the British model is
`en_GB-cori-medium`. They are synthesized voices. Words with several
pronunciations and proper names can depend on context; the feature does not
claim dictionary-recording accuracy for every spelling.

Inference is local and does not upload the text being read. Audio is cached
separately from learning records and does not mark words as known or reviewed.

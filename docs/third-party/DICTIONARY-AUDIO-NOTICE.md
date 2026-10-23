# Optional dictionary recordings

An explicitly requested English word may be looked up on English Wiktionary:
https://en.wiktionary.org/w/api.php
The selected audio file and its license metadata are obtained from Wikimedia
Commons: https://commons.wikimedia.org/w/api.php
Only a recording with an identifiable requested accent, author, file page and
eligible open license is played. No word lists are fetched in the background.

Recordings retain their individual CC BY, CC BY-SA, CC0 or public-domain terms;
the application does not assign a blanket license to these files. The playback
settings show each recording's author, license, Commons file page and license
link. Audio is decoded to mono 22,050 Hz PCM and modified by adding 350 ms of
leading silence and 200 ms of trailing silence. Speech is not cropped or sped
up at the normal playback setting. These modifications are identified in the UI.
Recordings with a share-alike license retain that license for the modified audio.
Preserve the stored credits and applicable license if redistributing recordings.

Downloaded recordings and their credits are cached together in
`data/dictionary-audio`, outside learning records, for up to 30 days and 128 MB.
They are fetched on demand and are not bundled as repository assets. The local
Piper and browser system voices are separate fallbacks, clearly identified in
the interface, and are not represented as dictionary recordings.

The optional decoder is miniaudio 1.71 (Python bindings, MIT):
https://github.com/irmen/pyminiaudio
The underlying miniaudio library is available under public-domain or MIT-0 terms:
https://github.com/mackron/miniaudio
Installed distributions retain their license files.

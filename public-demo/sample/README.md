# Public audio sample and recorded ASR result

Audio: LibriSpeech test-clean, utterance `61-70970-0012`, speaker `61`.
Dataset: Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev Khudanpur,
LibriSpeech, [OpenSLR 12](https://www.openslr.org/12).
License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Pinned dataset revision: `71cacbfb7e2354c4226d01e70d77d5fca3d04ba1`.

The retained study converted the original FLAC to M4A. `recording.json` names
the exact audio SHA-256, reference and recorded whisper.cpp tiny.en output.
The public workflow does not rerun speech inference or report a new WER.
Review/export requests use the original local upload and SQLite components,
then remove their temporary files and database rows.

# CPU human-speech diagnostic

This study exercises `Store -> enqueue_job -> process_next -> WhisperHTTPProvider
-> published transcript` with real human speech encoded as AAC/M4A. It complements
the synthetic macOS TTS study. It does not exercise the upload API or Android device.

## Frozen inputs

The source is [LibriSpeech test-clean](https://www.openslr.org/12), English read
audiobooks, CC BY 4.0. The Hugging Face `openslr/librispeech_asr` mirror is pinned
to revision `71cacbfb7e2354c4226d01e70d77d5fca3d04ba1`, file
`clean/test/0000.parquet`, SHA-256
`7113aa4c3cf963fb54697145719a7725f984c8836d1c494a554cbb9f1a017df0`.
Credit the LibriSpeech authors and source when redistributing derived audio.

Before inference, select two utterances per eligible speaker with duration from
3 through 12 seconds, ordered by SHA-256 of `speakup-human-v1:` followed by the
publisher utterance ID. Selection uses no transcript or model output. There are
80 clips from 40 speakers, totaling 532.495 seconds. Preserve the manifest and
its hash before running either arm. References come from the publisher, with no
new independent human adjudication. Model training overlap is unknown.

Install `pyarrow==26.0.0` for preparation; the worker and analysis use the standard
library. Download the pinned parquet outside the repository, then run:

```sh
python scripts/prepare_librispeech.py --parquet "$STUDY_INPUT" --out "$STUDY_AUDIO"
```

These variables must point to the downloaded parquet and a new external output
directory. Preparation refuses a different input hash and refuses overwriting.
It retains original FLAC and converted M4A hashes. Record the ffmpeg version.

## Run and compare

Start a local whisper.cpp server with a pinned model, `-ng -t 4 -bo 1 -bs 1 -nf
-l en --convert --host 127.0.0.1`, an owned port and an external temporary
directory. `-ng` disables GPU inference. Save the exact command, binary/model
hashes, server log and host details. Check that the log confirms CPU model buffers
and no GPU inference backend. Stop the owned server after each arm.

```sh
python scripts/human_speech_study.py run --manifest "$STUDY_AUDIO/manifest.json" \
  --endpoint "$LOCAL_INFERENCE_URL" --model-label whisper.cpp-tiny.en --out "$TINY_RESULT"
python scripts/human_speech_study.py run --manifest "$STUDY_AUDIO/manifest.json" \
  --endpoint "$LOCAL_INFERENCE_URL" --model-label whisper.cpp-base.en --out "$BASE_RESULT"
python scripts/human_speech_study.py compare --left "$TINY_RESULT/rows.jsonl" \
  --right "$BASE_RESULT/rows.jsonl" --out "$COMPARISON_FILE"
```

Run the matching server for each arm. Labels record configuration, not a server
attestation. The two arms compare sizes of the same Whisper model family, not
independent vendors. All endpoints must be loopback. Every audio hash is checked
before any request. Preserve raw output, worker database and summary. A failed
job makes the run exit nonzero and suppresses the accuracy summary; comparison
rejects incomplete jobs, mismatched references, speakers, durations or audio.

WER uses existing SpeakUp normalization: case, accents and punctuation are
removed, digits and apostrophes retained. The denominator pools reference words.
Uncertainty resamples whole speakers, retaining all their clips together, using
2,000 draws and seed 20261031. The paired difference uses the same sampled
speakers in both arms. Intervals are percentile diagnostics, not a broad accuracy
guarantee. Latency includes worker/database/HTTP and inference, but excludes
server startup. Real-time factor is processing seconds divided by source audio
seconds. Sequential arm order, system load and AAC duration padding limit timing
comparisons. No hardware-independent performance claim is supported.

This small clean audiobook sample does not establish noisy mobile speech,
conversational accents, clinical use, speaker diarization or physical-device
background behavior. Keep those gaps explicit.

## Local diagnostic result, October 31, 2026

Both arms completed 80 of 80 jobs, covering 1,471 normalized reference words.
On an Apple M3 Pro with four CPU inference threads:

| Arm | Edits | Pooled WER | Speaker bootstrap 95% interval | Median worker seconds | Pooled real-time factor |
| --- | ---: | ---: | --- | ---: | ---: |
| whisper.cpp tiny.en | 78 | 5.30% | 3.65% to 7.12% | 0.418 | 0.064 |
| whisper.cpp base.en | 55 | 3.74% | 2.55% to 5.10% | 0.732 | 0.113 |

Tiny minus base WER was 1.56 percentage points, with a paired speaker bootstrap
95% interval of 0.33 to 2.87 points. No configuration was tuned on these outputs.
This is a sample-specific result under the limits above. Tiny ran first, base
second; timing is descriptive. GPU inference was explicitly disabled in both.

Model SHA-256 values:

- tiny.en: `921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f`
- base.en: `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002`

Keep the frozen manifest, raw transcripts, worker databases, exact commands and
binary hashes with the private run evidence. The corpus is not bundled here.

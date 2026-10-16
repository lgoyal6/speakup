# Saved protocol replay

Generate traces from the same source revision with `python3 public-demo/generate_traces.py` from the repository root. The generator runs the native upload/store components against a temporary SQLite database and known synthetic bytes, then removes all temporary data. It writes only allow-listed offsets, state, digest and revision outcomes, plus relative code-path hashes. No audio, credentials, real user IDs, local paths, provider output or environment state are exported.

Serve this directory statically, or stage only index.html, style.css, app.js, traces.json and vercel.json for Vercel Hobby. No backend, microphone, file picker or upload API is shipped. Next step and Reset replay navigate saved records; they do not rerun a speech provider or Android client.

Use `python3 public-demo/generate_traces.py --check` to reconstruct and compare the saved traces. This is a component replay, not live voice processing or a quality benchmark.

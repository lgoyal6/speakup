#include "frame_engine.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <vector>

namespace {
void require(bool condition, const char* message) {
  if (!condition) { std::cerr << message << '\n'; std::exit(1); }
}

std::vector<std::int16_t> run_chunked(const std::vector<std::int16_t>& input,
                                      const std::vector<std::size_t>& chunks) {
  speakup::FrameEngine engine(16000, 160);
  std::vector<std::int16_t> output(input.size() + 160);
  std::size_t in_at = 0, out_at = 0, chunk_at = 0;
  while (in_at < input.size()) {
    const std::size_t n = std::min(chunks[chunk_at++ % chunks.size()], input.size() - in_at);
    const int written = engine.process(input.data() + in_at, n, output.data() + out_at,
                                       output.size() - out_at);
    require(written >= 0, "process rejected a valid chunk");
    in_at += n;
    out_at += static_cast<std::size_t>(written);
  }
  const int tail = engine.flush(output.data() + out_at, output.size() - out_at);
  require(tail >= 0, "flush failed");
  out_at += static_cast<std::size_t>(tail);
  output.resize(out_at);
  require(out_at == input.size(), "streaming lost or added samples");
  return output;
}
}

int main() {
  require(!speakup::FrameEngine(44100, 160).valid(), "unsupported sample rate accepted");
  speakup::FrameEngine bad(44100, 160);
  std::array<std::int16_t, 200> block{};
  require(bad.process(block.data(), block.size(), block.data(), block.size()) == -1,
          "invalid format did not fail explicitly");

  std::vector<std::int16_t> input(4781);
  for (std::size_t i = 0; i < input.size(); ++i)
    input[i] = static_cast<std::int16_t>(std::sin(i * .071) * (i % 3 ? 7000 : 600));
  const auto reference = run_chunked(input, {1600});
  const auto irregular = run_chunked(input, {17, 143, 521, 7, 319, 127, 1600});
  require(reference == irregular, "chunk boundaries changed output or stream state");
  require(reference.size() == input.size(), "flush did not preserve final tail length");

  speakup::FrameEngine capacity(16000, 160);
  std::array<std::int16_t, 320> two_frames{};
  std::array<std::int16_t, 160> one_frame{};
  require(capacity.process(two_frames.data(), two_frames.size(), one_frame.data(), one_frame.size()) == -2,
          "undersized output capacity was accepted");
  std::array<std::int16_t, 320> two_out{};
  require(capacity.process(two_frames.data(), two_frames.size(), two_out.data(), two_out.size()) == 320,
          "capacity failure mutated stream state");

  std::vector<std::int16_t> mixture;
  for (int frame = 0; frame < 20; ++frame)
    mixture.insert(mixture.end(), 160, 500);  // Calibration-only low-level room noise.
  for (int frame = 0; frame < 10; ++frame)
    mixture.insert(mixture.end(), 160, 1000);  // Persistent noise-only frames.
  for (int frame = 0; frame < 10; ++frame)
    mixture.insert(mixture.end(), 160, 8000);  // Speech-like high-energy frames.
  const auto filtered = run_chunked(mixture, {1600});
  std::int64_t noise_in = 0, noise_out = 0, speech_in = 0, speech_out = 0;
  for (std::size_t i = 20 * 160; i < 30 * 160; ++i) {
    noise_in += std::abs(mixture[i]); noise_out += std::abs(filtered[i]);
  }
  for (std::size_t i = 30 * 160; i < mixture.size(); ++i) {
    speech_in += std::abs(mixture[i]); speech_out += std::abs(filtered[i]);
  }
  require(noise_out < noise_in, "noise was not attenuated");
  require(speech_out > speech_in / 2, "speech-like high-energy frame was overattenuated");

  speakup::FrameEngine state(16000, 160);
  std::array<std::int16_t, 160> one{};
  one.fill(2000);
  std::array<std::int16_t, 160> processed{};
  require(state.process(one.data(), one.size(), processed.data(), processed.size()) == 160,
          "state test process failed");
  const auto before = state.stats();
  state.record_overrun();
  const auto after = state.stats();
  require(after[0] == before[0] && after[5] == before[5] + 1,
          "overrun counter did not record the event");
  state.reset();
  require(state.stats()[0] == 0 && state.stats()[5] == 0,
          "reset did not clear independent stream state");
  std::cout << "frame engine contract ok; frames=" << after[0]
            << " noise_attenuated=true chunk_invariant=true tail_preserved=true\n";
  return 0;
}

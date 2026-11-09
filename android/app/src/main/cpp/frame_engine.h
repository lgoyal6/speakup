#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace speakup {

class FrameEngine final {
 public:
  static constexpr int kSampleRate = 16000;
  static constexpr int kHopSamples = 160;  // 10 ms at 16 kHz.
  static constexpr int kMaxChunkSamples = 1600;  // 100 ms bounded JNI call.
  static constexpr std::size_t kMaxFrames = 30000;  // Five-minute capture cap.

  FrameEngine(int sample_rate, int hop_samples) noexcept;
  int process(const std::int16_t* input, std::size_t count,
              std::int16_t* output, std::size_t capacity) noexcept;
  int flush(std::int16_t* output, std::size_t capacity) noexcept;
  void reset() noexcept;
  void record_overrun() noexcept;
  std::array<std::int64_t, 6> stats() const noexcept;
  bool valid() const noexcept;

 private:
  void finish_frame(std::int16_t* output, std::size_t count) noexcept;
  int sample_rate_;
  int hop_samples_;
  std::array<std::int16_t, kHopSamples> pending_{};
  std::size_t pending_count_ = 0;
  std::array<float, 20> calibration_rms_{};
  std::size_t calibration_count_ = 0;
  float noise_rms_ = 200.0f;
  std::array<std::int64_t, kMaxFrames> frame_ns_{};
  std::size_t frame_count_ = 0;
  std::int64_t overrun_count_ = 0;
};

}  // namespace speakup

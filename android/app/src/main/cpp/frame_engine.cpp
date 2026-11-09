#include "frame_engine.h"

#include <algorithm>
#include <chrono>
#include <cmath>

namespace speakup {
namespace {
constexpr float kNoiseFloorMinimum = 120.0f;
constexpr float kNoiseFloorMaximum = 8000.0f;
constexpr float kGainFloor = 0.18f;
}

FrameEngine::FrameEngine(int sample_rate, int hop_samples) noexcept
    : sample_rate_(sample_rate), hop_samples_(hop_samples) {}

bool FrameEngine::valid() const noexcept {
  return sample_rate_ == kSampleRate && hop_samples_ == kHopSamples;
}

int FrameEngine::process(const std::int16_t* input, std::size_t count,
                         std::int16_t* output, std::size_t capacity) noexcept {
  if (!valid() || input == nullptr || output == nullptr || count > kMaxChunkSamples)
    return -1;
  const std::size_t expected = ((pending_count_ + count) / kHopSamples) * kHopSamples;
  if (capacity < expected || frame_count_ + expected / kHopSamples > kMaxFrames)
    return -2;
  std::size_t written = 0;
  for (std::size_t i = 0; i < count; ++i) {
    pending_[pending_count_++] = input[i];
    if (pending_count_ == kHopSamples) {
      finish_frame(output + written, pending_count_);
      written += pending_count_;
      pending_count_ = 0;
    }
  }
  return static_cast<int>(written);
}

int FrameEngine::flush(std::int16_t* output, std::size_t capacity) noexcept {
  if (!valid() || output == nullptr) return -1;
  if (capacity < pending_count_) return -2;
  if (pending_count_ == 0) return 0;
  finish_frame(output, pending_count_);
  const int written = static_cast<int>(pending_count_);
  pending_count_ = 0;
  return written;
}

void FrameEngine::finish_frame(std::int16_t* output, std::size_t count) noexcept {
  const auto start = std::chrono::steady_clock::now();
  double sum_squares = 0.0;
  for (std::size_t i = 0; i < count; ++i) {
    const double sample = static_cast<double>(pending_[i]);
    sum_squares += sample * sample;
  }
  const float rms = count == 0 ? 0.0f : static_cast<float>(std::sqrt(sum_squares / count));
  float gain = 1.0f;
  if (calibration_count_ < calibration_rms_.size()) {
    calibration_rms_[calibration_count_++] = rms;
  } else {
    if (calibration_count_ == calibration_rms_.size()) {
      const float minimum = *std::min_element(calibration_rms_.begin(), calibration_rms_.end());
      noise_rms_ = std::clamp(minimum * 1.4f, kNoiseFloorMinimum, kNoiseFloorMaximum);
      ++calibration_count_;
    }
    if (rms < noise_rms_ * 2.0f)
      noise_rms_ = std::clamp(noise_rms_ * 0.96f + rms * 0.04f,
                              kNoiseFloorMinimum, kNoiseFloorMaximum);
    if (rms > 0.0f)
      gain = std::clamp(1.0f - 1.25f * noise_rms_ / rms, kGainFloor, 1.0f);
  }
  for (std::size_t i = 0; i < count; ++i) {
    const float scaled = static_cast<float>(pending_[i]) * gain;
    output[i] = static_cast<std::int16_t>(std::clamp(scaled, -32768.0f, 32767.0f));
  }
  const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now() - start).count();
  if (frame_count_ < frame_ns_.size()) frame_ns_[frame_count_++] = elapsed;
}

void FrameEngine::reset() noexcept {
  pending_count_ = 0;
  calibration_count_ = 0;
  noise_rms_ = 200.0f;
  frame_count_ = 0;
  overrun_count_ = 0;
}

void FrameEngine::record_overrun() noexcept { ++overrun_count_; }

std::array<std::int64_t, 6> FrameEngine::stats() const noexcept {
  std::array<std::int64_t, 6> result{};
  result[0] = static_cast<std::int64_t>(frame_count_);
  result[5] = overrun_count_;
  if (frame_count_ == 0) return result;
  std::array<std::int64_t, kMaxFrames> sorted{};
  std::copy_n(frame_ns_.begin(), frame_count_, sorted.begin());
  std::sort(sorted.begin(), sorted.begin() + frame_count_);
  const auto percentile = [&](double p) -> std::int64_t {
    const auto index = static_cast<std::size_t>(std::ceil(p * frame_count_)) - 1;
    return sorted[std::min(index, frame_count_ - 1)];
  };
  result[1] = percentile(.50);
  result[2] = percentile(.95);
  result[3] = percentile(.99);
  result[4] = sorted[frame_count_ - 1];
  return result;
}

}  // namespace speakup

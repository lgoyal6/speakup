#include "../android/app/src/main/cpp/frame_engine.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {
constexpr std::size_t kChunkSamples = speakup::FrameEngine::kMaxChunkSamples;
constexpr std::size_t kChunkBytes = kChunkSamples * sizeof(std::int16_t);

bool write_samples(const std::int16_t* samples, std::size_t count) {
  std::array<unsigned char, kChunkBytes + 320> bytes{};
  if (count * 2 > bytes.size()) return false;
  for (std::size_t i = 0; i < count; ++i) {
    const auto value = static_cast<std::uint16_t>(samples[i]);
    bytes[i * 2] = static_cast<unsigned char>(value & 0xff);
    bytes[i * 2 + 1] = static_cast<unsigned char>(value >> 8);
  }
  return std::fwrite(bytes.data(), 1, count * 2, stdout) == count * 2;
}
}

int main(int argc, char** argv) {
  const std::string mode = argc > 1 ? argv[1] : "gate";
  if (mode != "gate" && mode != "passthrough") {
    std::cerr << "usage: frame_filter_cli [gate|passthrough]\n";
    return 2;
  }
  speakup::FrameEngine engine(speakup::FrameEngine::kSampleRate,
                              speakup::FrameEngine::kHopSamples);
  std::array<unsigned char, kChunkBytes> raw{};
  std::array<std::int16_t, kChunkSamples> input{};
  std::array<std::int16_t, kChunkSamples> output{};
  std::array<std::int16_t, speakup::FrameEngine::kHopSamples> tail{};
  std::int64_t chunks = 0;
  std::int64_t samples = 0;
  std::int64_t output_samples = 0;
  std::int64_t elapsed_us = 0;
  while (true) {
    const auto read = std::fread(raw.data(), 1, raw.size(), stdin);
    if (read == 0) {
      if (std::ferror(stdin)) return 3;
      break;
    }
    if (read % 2 != 0) {
      std::cerr << "input must contain complete little-endian int16 samples\n";
      return 4;
    }
    const auto count = read / 2;
    for (std::size_t i = 0; i < count; ++i) {
      const auto value = static_cast<std::uint16_t>(
          static_cast<std::uint16_t>(raw[i * 2]) |
          (static_cast<std::uint16_t>(raw[i * 2 + 1]) << 8));
      std::memcpy(&input[i], &value, sizeof(value));
    }
    const auto started = std::chrono::steady_clock::now();
    int written = 0;
    if (mode == "gate") {
      written = engine.process(input.data(), count, output.data(), output.size());
    } else {
      std::copy_n(input.begin(), count, output.begin());
      written = static_cast<int>(count);
    }
    elapsed_us += std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started).count();
    if (written < 0 || !write_samples(output.data(), static_cast<std::size_t>(written))) {
      std::cerr << "frame engine or output write failed: " << written << " count=" << count
                << " valid=" << engine.valid() << '\n';
      return 5;
    }
    output_samples += written;
    samples += static_cast<std::int64_t>(count);
    ++chunks;
  }
  int tail_count = 0;
  if (mode == "gate") {
    tail_count = engine.flush(tail.data(), tail.size());
    if (tail_count < 0 || !write_samples(tail.data(), static_cast<std::size_t>(tail_count))) {
      std::cerr << "frame engine flush failed: " << tail_count << '\n';
      return 6;
    }
    output_samples += tail_count;
  }
  const auto stats = engine.stats();
  std::cerr << "{\"mode\":\"" << mode << "\",\"samples\":" << samples
            << ",\"output_samples\":" << output_samples
            << ",\"chunks\":" << chunks << ",\"chunk_processing_us\":" << elapsed_us
            << ",\"frames\":" << stats[0] << ",\"frame_ns_p50\":" << stats[1]
            << ",\"frame_ns_p95\":" << stats[2] << ",\"frame_ns_p99\":" << stats[3]
            << ",\"frame_ns_max\":" << stats[4] << ",\"overruns\":" << stats[5] << "}\n";
  return 0;
}

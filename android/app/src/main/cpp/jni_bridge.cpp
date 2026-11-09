#include "frame_engine.h"

#include <jni.h>
#include <array>
#include <cstdint>
#include <memory>
#include <new>

namespace {
speakup::FrameEngine* engine(jlong handle) noexcept {
  return reinterpret_cast<speakup::FrameEngine*>(static_cast<std::uintptr_t>(handle));
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeCreate(JNIEnv*, jobject,
                                                       jint sample_rate, jint hop) {
  auto* created = new (std::nothrow) speakup::FrameEngine(sample_rate, hop);
  if (created == nullptr || !created->valid()) {
    delete created;
    return 0;
  }
  return static_cast<jlong>(reinterpret_cast<std::uintptr_t>(created));
}

extern "C" JNIEXPORT jint JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeProcess(JNIEnv* env, jobject,
    jlong handle, jshortArray input, jint count, jshortArray output) {
  auto* state = engine(handle);
  if (state == nullptr || input == nullptr || output == nullptr || count < 0 ||
      count > speakup::FrameEngine::kMaxChunkSamples ||
      env->GetArrayLength(input) < count || env->GetArrayLength(output) < count)
    return -1;
  std::array<jshort, speakup::FrameEngine::kMaxChunkSamples> in{};
  std::array<jshort, speakup::FrameEngine::kMaxChunkSamples> out{};
  env->GetShortArrayRegion(input, 0, count, in.data());
  if (env->ExceptionCheck()) return -1;
  const int written = state->process(reinterpret_cast<const std::int16_t*>(in.data()),
      static_cast<std::size_t>(count), reinterpret_cast<std::int16_t*>(out.data()), out.size());
  if (written >= 0) env->SetShortArrayRegion(output, 0, written, out.data());
  return written;
}

extern "C" JNIEXPORT jint JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeFlush(JNIEnv* env, jobject,
    jlong handle, jshortArray output) {
  auto* state = engine(handle);
  if (state == nullptr || output == nullptr ||
      env->GetArrayLength(output) < speakup::FrameEngine::kHopSamples) return -1;
  std::array<jshort, speakup::FrameEngine::kHopSamples> out{};
  const int written = state->flush(reinterpret_cast<std::int16_t*>(out.data()), out.size());
  if (written >= 0) env->SetShortArrayRegion(output, 0, written, out.data());
  return written;
}

extern "C" JNIEXPORT jlongArray JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeStats(JNIEnv* env, jobject, jlong handle) {
  auto* state = engine(handle);
  if (state == nullptr) return nullptr;
  const auto values = state->stats();
  jlongArray result = env->NewLongArray(static_cast<jsize>(values.size()));
  if (result != nullptr) env->SetLongArrayRegion(result, 0,
      static_cast<jsize>(values.size()), reinterpret_cast<const jlong*>(values.data()));
  return result;
}

extern "C" JNIEXPORT void JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeReset(JNIEnv*, jobject, jlong handle) {
  if (auto* state = engine(handle)) state->reset();
}

extern "C" JNIEXPORT void JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeRecordOverrun(JNIEnv*, jobject, jlong handle) {
  if (auto* state = engine(handle)) state->record_overrun();
}

extern "C" JNIEXPORT void JNICALL
Java_com_speakup_audio_NativeFrameEngine_nativeDestroy(JNIEnv*, jobject, jlong handle) {
  delete engine(handle);
}

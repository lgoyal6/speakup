package com.speakup.audio

/** One independent stream of bounded 16 kHz mono PCM frames. */
internal class NativeFrameEngine(sampleRate: Int = SAMPLE_RATE, hopSamples: Int = HOP_SAMPLES) : AutoCloseable {
    private var handle: Long

    init {
        handle = nativeCreate(sampleRate, hopSamples)
        check(handle != 0L) { "Unsupported DSP format or native engine allocation failed" }
    }

    /** Returns samples emitted; a partial frame remains buffered until process/flush. */
    fun process(input: ShortArray, count: Int, output: ShortArray): Int {
        check(handle != 0L) { "engine is closed" }
        require(count in 0..input.size && count <= MAX_CHUNK_SAMPLES)
        require(output.size >= count)
        val written = nativeProcess(handle, input, count, output)
        check(written >= 0) { "native process rejected input or capacity" }
        return written
    }

    fun flush(output: ShortArray = ShortArray(HOP_SAMPLES)): Int {
        check(handle != 0L) { "engine is closed" }
        require(output.size >= HOP_SAMPLES)
        val written = nativeFlush(handle, output)
        check(written >= 0) { "native flush failed" }
        return written
    }

    fun stats(): LongArray {
        check(handle != 0L) { "engine is closed" }
        return checkNotNull(nativeStats(handle))
    }

    fun recordOverrun() {
        check(handle != 0L) { "engine is closed" }
        nativeRecordOverrun(handle)
    }

    fun reset() {
        check(handle != 0L) { "engine is closed" }
        nativeReset(handle)
    }

    override fun close() {
        val old = handle
        if (old != 0L) {
            handle = 0L
            nativeDestroy(old)
        }
    }

    private external fun nativeCreate(sampleRate: Int, hopSamples: Int): Long
    private external fun nativeProcess(handle: Long, input: ShortArray, count: Int, output: ShortArray): Int
    private external fun nativeFlush(handle: Long, output: ShortArray): Int
    private external fun nativeStats(handle: Long): LongArray?
    private external fun nativeRecordOverrun(handle: Long)
    private external fun nativeReset(handle: Long)
    private external fun nativeDestroy(handle: Long)

    companion object {
        const val SAMPLE_RATE = 16000
        const val HOP_SAMPLES = 160
        const val MAX_CHUNK_SAMPLES = 1600
        init { System.loadLibrary("speakup_dsp") }
    }
}

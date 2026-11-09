package com.speakup.audio

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

private const val SAMPLE_RATE = 16000
private const val CHUNK_SAMPLES = 1600 // 100 ms; ten native 10 ms frames.
private const val POOL_SIZE = 8
private const val MAX_SAMPLES = SAMPLE_RATE * 60 * 5
private const val ENGINE_REVISION = "speakup-adaptive-frame-gate-v1"

data class PcmTake(
    val id: String,
    val status: String,
    val originalPath: String,
    val enhancedPath: String,
    val manifestPath: String,
    val sampleCount: Long,
    val sourceHash: String?,
    val enhancedHash: String?,
    val error: String?,
)

/** Durable app-private takes; PCM originals are never inserted into the upload queue. */
internal class PcmTakeStore(context: Context) {
    private val root = File(context.filesDir, "audio-enhancement")

    fun create(): Pair<File, String> {
        check(root.mkdirs() || root.isDirectory) { "cannot create private audio directory" }
        val id = UUID.randomUUID().toString()
        val directory = File(root, id)
        check(directory.mkdir()) { "cannot create audio take" }
        return directory to id
    }

    fun writeManifest(directory: File, take: JSONObject) {
        val tmp = File(directory, "manifest.json.tmp")
        FileOutputStream(tmp).use { stream ->
            stream.write(take.toString(2).toByteArray(Charsets.UTF_8))
            stream.fd.sync()
        }
        val target = File(directory, "manifest.json")
        Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING)
    }

    fun listAndRecover(): List<PcmTake> {
        if (!root.isDirectory) return emptyList()
        return root.listFiles()?.asSequence()?.filter { it.isDirectory }
            ?.mapNotNull { directory ->
                val file = File(directory, "manifest.json")
                if (!file.isFile) return@mapNotNull null
                try {
                    val json = JSONObject(file.readText())
                    if (json.optString("status") == "RECORDING" || json.optString("status") == "FINALIZING") {
                        json.put("status", "INTERRUPTED")
                        json.put("error", "capture ended before durable finalization")
                        writeManifest(directory, json)
                    }
                    PcmTake(id = json.getString("id"), status = json.getString("status"),
                        originalPath = File(directory, "original.pcm").absolutePath,
                        enhancedPath = File(directory, "enhanced.pcm").absolutePath,
                        manifestPath = file.absolutePath, sampleCount = json.optLong("sample_count"),
                        sourceHash = json.optString("source_sha256").ifBlank { null },
                        enhancedHash = json.optString("enhanced_sha256").ifBlank { null },
                        error = json.optString("error").ifBlank { null })
                } catch (_: Exception) { null }
            }?.sortedByDescending { File(it.manifestPath).lastModified() }?.toList() ?: emptyList()
    }

    fun delete(id: String) {
        require(Regex("[a-f0-9-]{36}").matches(id)) { "invalid audio take id" }
        val base = root.canonicalFile
        val target = File(root, id).canonicalFile
        require(target.parentFile == base) { "audio take path escapes private directory" }
        if (target.exists() && !target.deleteRecursively()) throw IOException("cannot delete audio take")
    }

    fun playback(path: String) {
        val file = File(path).canonicalFile
        require(file.parentFile?.parentFile == root.canonicalFile) { "audio file is outside private store" }
        require(file.isFile && file.length() <= MAX_SAMPLES * 2L) { "invalid or oversized PCM file" }
        val minimum = AudioTrack.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT)
        check(minimum > 0) { "PCM playback format is unavailable" }
        val track = AudioTrack.Builder().setAudioFormat(AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(maxOf(minimum, CHUNK_SAMPLES * 2))
            .setTransferMode(AudioTrack.MODE_STREAM).build()
        try {
            track.play()
            val buffer = ByteArray(CHUNK_SAMPLES * 2)
            FileInputStream(file).use { input ->
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    var offset = 0
                    while (offset < n) {
                        val written = track.write(buffer, offset, n - offset, AudioTrack.WRITE_BLOCKING)
                        if (written <= 0) throw IOException("PCM playback write failed")
                        offset += written
                    }
                }
            }
            val deadline = SystemClock.elapsedRealtime() + 30_000
            while (track.playbackHeadPosition.toLong() * 2 < file.length() &&
                SystemClock.elapsedRealtime() < deadline) Thread.sleep(10)
        } finally {
            try { track.stop() } catch (_: IllegalStateException) { }
            track.release()
        }
    }
}

/** Reads mic PCM on one blocking thread and processes/writes on a bounded consumer thread. */
internal class PcmCaptureSession(
    private val context: Context,
    private val store: PcmTakeStore,
    private val onState: (String) -> Unit,
    private val onFinished: (PcmTake?) -> Unit,
) {
    private class Chunk(val input: ShortArray = ShortArray(CHUNK_SAMPLES),
                        val output: ShortArray = ShortArray(CHUNK_SAMPLES),
                        val bytes: ByteArray = ByteArray(CHUNK_SAMPLES * 2),
                        var count: Int = 0, val end: Boolean = false)
    private val end = Chunk(end = true)
    private val free = ArrayBlockingQueue<Chunk>(POOL_SIZE)
    private val ready = ArrayBlockingQueue<Chunk>(POOL_SIZE)
    private val stopping = AtomicBoolean(false)
    @Volatile private var failure: Throwable? = null
    @Volatile private var audioRecord: AudioRecord? = null
    @Volatile private var stopReason = "USER_STOP"
    private val created = store.create()
    private val directory = created.first
    private val id = created.second
    private val originalFile = File(directory, "original.pcm")
    private val enhancedFile = File(directory, "enhanced.pcm")
    private val engine = NativeFrameEngine(SAMPLE_RATE, NativeFrameEngine.HOP_SAMPLES)
    private val initialManifest = baseManifest("RECORDING")
    private var captureThread: Thread? = null
    private var writerThread: Thread? = null
    private var recordStartedAtNs = 0L

    init { repeat(POOL_SIZE) { free.add(Chunk()) } }

    fun start() {
        try {
            store.writeManifest(directory, initialManifest)
            val minimum = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT)
            check(minimum > 0) { "AudioRecord does not support 16 kHz mono PCM" }
            val bufferBytes = maxOf(minimum, CHUNK_SAMPLES * 2 * 8)
            val record = AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferBytes)
            check(record.state == AudioRecord.STATE_INITIALIZED) { "AudioRecord initialization failed" }
            audioRecord = record
            record.startRecording()
            check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "microphone did not start" }
            recordStartedAtNs = System.nanoTime()
            writerThread = Thread(::writeLoop, "speakup-pcm-writer").apply { start() }
            captureThread = Thread(::captureLoop, "speakup-audiorecord").apply { start() }
            onState("RECORDING")
        } catch (error: Throwable) {
            failure = error
            stopReason = "START_FAILURE"
            stopping.set(true)
            releaseRecord()
            finishManifest(0, 0, "FAILED")
            engine.close()
            throw error
        }
    }

    fun stop(reason: String = "USER_STOP"): PcmTake? {
        stopReason = reason
        stopping.set(true)
        stopRecord()
        captureThread?.join(5_000)
        writerThread?.join(10_000)
        if (captureThread?.isAlive == true || writerThread?.isAlive == true) {
            failure = IOException("capture shutdown exceeded bounded join deadline")
            stopRecord()
            captureThread?.interrupt()
            writerThread?.interrupt()
            captureThread?.join(1_000)
            writerThread?.join(1_000)
        }
        return store.listAndRecover().firstOrNull { it.id == id }
    }

    private fun captureLoop() {
        var samples = 0L
        try {
            while (!stopping.get() && samples < MAX_SAMPLES) {
                val chunk = free.poll(250, TimeUnit.MILLISECONDS)
                if (chunk == null) {
                    engine.recordOverrun()
                    throw IOException("bounded PCM queue starved: disk/processor exceeded 800 ms")
                }
                val count = audioRecord?.read(chunk.input, 0, chunk.input.size,
                    AudioRecord.READ_BLOCKING) ?: AudioRecord.ERROR_INVALID_OPERATION
                if (count < 0) {
                    free.offer(chunk)
                    if (stopping.get()) break
                    throw IOException("AudioRecord read failed with code $count")
                }
                if (count == 0) { free.offer(chunk); continue }
                chunk.count = count
                if (!ready.offer(chunk)) {
                    free.offer(chunk)
                    engine.recordOverrun()
                    throw IOException("bounded PCM queue full; stopping rather than dropping samples")
                }
                samples += count
            }
            if (!stopping.get() && samples >= MAX_SAMPLES) {
                stopReason = "MAX_DURATION"
                stopping.set(true)
                stopRecord()
            }
        } catch (error: Throwable) {
            if (!stopping.get()) failure = error
            stopping.set(true)
            stopRecord()
        } finally {
            releaseRecord()
            try { ready.put(end) } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
        }
    }

    private fun writeLoop() {
        var inputSamples = 0L
        var outputSamples = 0L
        var source: FileOutputStream? = null
        var derivative: FileOutputStream? = null
        try {
            source = FileOutputStream(originalFile)
            derivative = FileOutputStream(enhancedFile)
        } catch (error: Throwable) {
            failure = error
            stopping.set(true)
            stopRecord()
        }
        try {
            while (true) {
                val chunk = ready.take()
                if (chunk.end) break
                try {
                    if (failure == null && source != null && derivative != null) {
                        encodeLittleEndian(chunk.input, chunk.count, chunk.bytes)
                        source.write(chunk.bytes, 0, chunk.count * 2)
                        inputSamples += chunk.count
                        val written = engine.process(chunk.input, chunk.count, chunk.output)
                        encodeLittleEndian(chunk.output, written, chunk.bytes)
                        derivative.write(chunk.bytes, 0, written * 2)
                        outputSamples += written
                    }
                } catch (error: Throwable) {
                    failure = error
                    stopping.set(true)
                    stopRecord()
                } finally { chunk.count = 0; free.offer(chunk) }
            }
            if (failure == null && derivative != null) {
                val tailBuffer = ShortArray(NativeFrameEngine.HOP_SAMPLES)
                val tailCount = engine.flush(tailBuffer)
                if (tailCount > 0) {
                    val bytes = ByteArray(tailCount * 2)
                    encodeLittleEndian(tailBuffer, tailCount, bytes)
                    derivative.write(bytes)
                    outputSamples += tailCount
                }
            }
        } catch (error: Throwable) {
            failure = failure ?: error
            stopping.set(true)
            stopRecord()
        } finally {
            try { source?.fd?.sync() } catch (error: Throwable) { failure = failure ?: error }
            try { derivative?.fd?.sync() } catch (error: Throwable) { failure = failure ?: error }
            try { source?.close() } catch (_: Throwable) { }
            try { derivative?.close() } catch (_: Throwable) { }
            val status = when {
                failure != null -> "FAILED"
                stopReason == "LIFECYCLE_INTERRUPT" -> "INTERRUPTED"
                else -> "READY"
            }
            finishManifest(inputSamples, outputSamples, status)
            engine.close()
            onFinished(store.listAndRecover().firstOrNull { it.id == id })
        }
    }

    private fun finishManifest(inputSamples: Long, outputSamples: Long, status: String) {
        try {
            val stats = engine.stats()
            val manifest = baseManifest(status)
                .put("completed_at_epoch_ms", System.currentTimeMillis())
                .put("stop_reason", stopReason)
                .put("sample_count", inputSamples)
                .put("enhanced_sample_count", outputSamples)
                .put("duration_seconds", inputSamples.toDouble() / SAMPLE_RATE)
                .put("source_sha256", originalFile.takeIf { it.isFile }?.let(::fileSha256) ?: JSONObject.NULL)
                .put("enhanced_sha256", enhancedFile.takeIf { it.isFile }?.let(::fileSha256) ?: JSONObject.NULL)
                .put("frame_stats_ns", JSONObject().put("frames", stats[0]).put("p50", stats[1])
                    .put("p95", stats[2]).put("p99", stats[3]).put("max", stats[4]))
                .put("queue_overruns", stats[5])
                .put("read_elapsed_ms", if (recordStartedAtNs == 0L) 0L
                    else ((System.nanoTime() - recordStartedAtNs).coerceAtLeast(0)) / 1_000_000)
            failure?.let { manifest.put("error", (it.message ?: it.javaClass.simpleName).take(500)) }
            store.writeManifest(directory, manifest)
        } catch (error: Throwable) {
            failure = failure ?: error
        }
    }

    private fun baseManifest(status: String) = JSONObject()
        .put("schema_version", 1).put("id", id).put("status", status)
        .put("created_at_epoch_ms", System.currentTimeMillis())
        .put("original_format", "s16le_pcm").put("enhanced_format", "s16le_pcm")
        .put("sample_rate_hz", SAMPLE_RATE).put("channels", 1).put("frame_samples", 160)
        .put("hop_samples", 160).put("algorithmic_lookahead_samples", 0)
        .put("frame_accumulation_samples", 160)
        .put("processor_revision", ENGINE_REVISION)
        .put("processor_description", "causal adaptive frame gate; classical baseline, not a neural denoiser")
        .put("model_hash", JSONObject.NULL).put("uploaded", false)
        .put("max_duration_seconds", 300).put("buffer_chunks", POOL_SIZE)

    private fun stopRecord() {
        val record = audioRecord ?: return
        try { if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop() }
        catch (_: IllegalStateException) { }
    }

    private fun releaseRecord() {
        val record = audioRecord ?: return
        audioRecord = null
        try { record.release() } catch (_: Throwable) { }
    }

    private fun encodeLittleEndian(input: ShortArray, count: Int, output: ByteArray) {
        require(count >= 0 && count <= input.size && output.size >= count * 2)
        for (i in 0 until count) {
            val sample = input[i].toInt()
            output[i * 2] = (sample and 0xff).toByte()
            output[i * 2 + 1] = ((sample ushr 8) and 0xff).toByte()
        }
    }

    private fun fileSha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}

/** UI-facing async controls; the existing AAC upload flow is never modified. */
class PcmCaptureController(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext
    private val store = PcmTakeStore(appContext)
    private val executor = Executors.newSingleThreadExecutor()
    private val _state = MutableStateFlow("IDLE")
    private val _takes = MutableStateFlow(store.listAndRecover())
    val state: StateFlow<String> = _state
    val takes: StateFlow<List<PcmTake>> = _takes
    @Volatile private var session: PcmCaptureSession? = null

    fun start() {
        if (_state.value == "RECORDING" || _state.value == "STARTING") return
        _state.value = "STARTING"
        executor.execute {
            try {
                val active = PcmCaptureSession(appContext, store,
                    onState = { _state.value = it },
                    onFinished = { take ->
                        session = null
                        _takes.value = store.listAndRecover()
                        _state.value = take?.status ?: "FAILED"
                    })
                session = active
                active.start()
            } catch (_: Throwable) { session = null; _state.value = "FAILED"; _takes.value = store.listAndRecover() }
        }
    }

    fun stop(reason: String = "USER_STOP") {
        if (_state.value == "FINALIZING") return
        val active = session ?: return
        _state.value = "FINALIZING"
        executor.execute {
            val take = try { active.stop(reason) } catch (_: Throwable) { null }
            session = null
            _takes.value = store.listAndRecover()
            _state.value = take?.status ?: "FAILED"
        }
    }

    fun interrupt() = stop("LIFECYCLE_INTERRUPT")
    fun delete(id: String) {
        executor.execute {
            try { store.delete(id); _takes.value = store.listAndRecover() }
            catch (_: Throwable) { _state.value = "DELETE_FAILED" }
        }
    }
    fun play(path: String) { executor.execute { try { store.playback(path) } catch (_: Throwable) { _state.value = "PLAYBACK_FAILED" } } }

    override fun close() {
        interrupt()
        executor.shutdown()
    }
}

package com.speakup.audio

import android.Manifest
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class PcmCaptureTest {
    @get:Rule val microphonePermission: GrantPermissionRule =
        GrantPermissionRule.grant(Manifest.permission.RECORD_AUDIO)

    @Test fun audioRecordPersistsOriginalAndSeparateDerivativeWithoutUpload() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PcmTakeStore(context)
        var callbackTake: PcmTake? = null
        val session = PcmCaptureSession(context, store, {}, { callbackTake = it })
        session.start()
        SystemClock.sleep(1300)
        val take = checkNotNull(session.stop())
        assertEquals("READY", take.status)
        assertTrue("capture returned no samples", take.sampleCount >= 16000)
        val original = File(take.originalPath)
        val enhanced = File(take.enhancedPath)
        assertTrue(original.isFile)
        assertTrue(enhanced.isFile)
        assertNotEquals(original.canonicalPath, enhanced.canonicalPath)
        assertEquals(take.sampleCount * 2, original.length())
        assertEquals(original.length(), enhanced.length())
        assertEquals(take.sourceHash, sha256(original))
        assertEquals(take.enhancedHash, sha256(enhanced))
        val manifest = JSONObject(File(take.manifestPath).readText())
        assertEquals(16000, manifest.getInt("sample_rate_hz"))
        assertEquals(1, manifest.getInt("channels"))
        assertEquals(0, manifest.getInt("algorithmic_lookahead_samples"))
        assertEquals(160, manifest.getInt("frame_accumulation_samples"))
        assertEquals("speakup-adaptive-frame-gate-v1", manifest.getString("processor_revision"))
        assertEquals(false, manifest.getBoolean("uploaded"))
        assertEquals(take.sampleCount, manifest.getLong("enhanced_sample_count"))
        store.delete(take.id)
        assertFalse(original.exists())
        assertFalse(enhanced.exists())
        assertFalse(File(take.manifestPath).exists())
        assertNotNull(callbackTake)
    }

    @Test fun lifecycleInterruptionIsPersistedAndOriginalIsRetained() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PcmTakeStore(context)
        val session = PcmCaptureSession(context, store, {}, {})
        session.start()
        SystemClock.sleep(400)
        val take = checkNotNull(session.stop("LIFECYCLE_INTERRUPT"))
        assertEquals("INTERRUPTED", take.status)
        assertTrue(File(take.originalPath).isFile)
        assertEquals(take.sampleCount * 2, File(take.originalPath).length())
        store.delete(take.id)
    }

    @Test fun streamContractPreservesSamplesAcrossOddChunkBoundaries() {
        val input = ShortArray(1733) { ((it * 37) % 8000 - 4000).toShort() }
        fun run(chunkSizes: IntArray): ShortArray {
            val engine = NativeFrameEngine()
            val result = ShortArray(input.size)
            val output = ShortArray(1600)
            var source = 0
            var target = 0
            var chunk = 0
            while (source < input.size) {
                val count = minOf(chunkSizes[chunk++ % chunkSizes.size], input.size - source)
                val part = input.copyOfRange(source, source + count)
                val written = engine.process(part, count, output)
                output.copyInto(result, target, 0, written)
                source += count
                target += written
            }
            val tail = ShortArray(NativeFrameEngine.HOP_SAMPLES)
            val count = engine.flush(tail)
            tail.copyInto(result, target, 0, count)
            target += count
            engine.close()
            assertEquals(input.size, target)
            return result
        }
        assertArrayEquals(run(intArrayOf(1600)), run(intArrayOf(1, 159, 73, 401, 7)))
    }

    @Test fun staleRecordingManifestReopensAsInterrupted() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PcmTakeStore(context)
        val (directory, id) = store.create()
        store.writeManifest(directory, JSONObject().put("schema_version", 1)
            .put("id", id).put("status", "RECORDING").put("sample_count", 0))
        val take = store.listAndRecover().first { it.id == id }
        assertEquals("INTERRUPTED", take.status)
        store.delete(id)
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
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

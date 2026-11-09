// Show recording controls and editable offline history.
// AudioRecorder owns capture; Room and UploadWorker retain durable state.
// Interrupted drafts require review before upload.
package com.speakup

import android.Manifest
import android.os.Bundle
import android.widget.Toast
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.speakup.recording.AudioRecorder
import com.speakup.audio.PcmCaptureController
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.input.PasswordVisualTransformation
import com.speakup.data.RecordingDatabase
import com.speakup.data.RecordingEntity
import com.speakup.recording.recordingStopWithCleanup
import com.speakup.security.Credentials
import com.speakup.sync.UploadWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.launch
import java.io.File

/** UI state is ephemeral; Room/WorkManager own the durable recording and sync state. */
class MainActivity : ComponentActivity() {
    private lateinit var recorder: AudioRecorder
    private lateinit var database: RecordingDatabase
    private lateinit var pcmCapture: PcmCaptureController
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState); recorder = AudioRecorder(this)
        pcmCapture = PcmCaptureController(this)
        // A row that was LOCAL_DRAFT when the process died is retained and made resumable.
        database = RecordingDatabase.open(this)
        runBlocking(Dispatchers.IO) {
            database.recordings().interrupted().forEach { database.recordings().interrupted(it.id, "recording interrupted; review before upload", System.currentTimeMillis()) }
        }
        setContent { SpeakUpScreen(database, onStart = { recorder.start() }, onStop = { recorder.stop() }, pcmCapture = pcmCapture) }
    }
    override fun onStop() { if (::pcmCapture.isInitialized) pcmCapture.interrupt(); super.onStop() }
    override fun onDestroy() { recorder.release(); if (::pcmCapture.isInitialized) pcmCapture.close(); database.close(); super.onDestroy() }
}

@Composable
fun SpeakUpScreen(database: RecordingDatabase? = null, onStart: () -> Unit = {}, onStop: () -> Unit = {}, pcmCapture: PcmCaptureController? = null) {
    val context = LocalContext.current
    val savedConnection = remember { Credentials(context).load() }
    var baseUrl by remember { mutableStateOf(savedConnection?.baseUrl ?: "") }
    var token by remember { mutableStateOf(savedConnection?.token ?: "") }
    var recording by remember { mutableStateOf(false) }
    var permissionDenied by remember { mutableStateOf(false) }
    var pcmPermissionDenied by remember { mutableStateOf(false) }
    val pcmState = pcmCapture?.state?.collectAsState()?.value ?: "UNAVAILABLE"
    val pcmTakes = pcmCapture?.takes?.collectAsState()?.value ?: emptyList()
    val scope = rememberCoroutineScope()
    val recordings = database?.recordings()?.history()?.collectAsState(initial = emptyList())?.value ?: emptyList()
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionDenied = !granted
        if (granted) try { onStart(); recording = true } catch (error: Exception) { Toast.makeText(context, error.message ?: "recording failed", Toast.LENGTH_SHORT).show() }
    }
    val pcmPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        pcmPermissionDenied = !granted
        if (granted) pcmCapture?.start()
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("SpeakUp", style = MaterialTheme.typography.headlineMedium)
        Text("Record offline, review the transcript, then sync your changes.")
        OutlinedTextField(value = baseUrl, onValueChange = { baseUrl = it }, label = { Text("API origin") }, singleLine = true)
        OutlinedTextField(value = token, onValueChange = { token = it }, label = { Text("Bearer token") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
        OutlinedButton(onClick = {
            try {
                Credentials(context).save(baseUrl, token)
                Toast.makeText(context, "Connection saved", Toast.LENGTH_SHORT).show()
                scope.launch {
                    database?.let { resumePendingSync(context, it) }
                }
            } catch (error: Exception) { Toast.makeText(context, error.message ?: "Invalid connection", Toast.LENGTH_SHORT).show() }
        }) { Text("Save connection and resume sync") }
        if (permissionDenied) Text("Microphone permission is required to record.", color = MaterialTheme.colorScheme.error)
        Button(onClick = {
            try {
                if (recording) {
                    recordingStopWithCleanup(onStop, onFailure = {}, cleanup = { recording = false })
                }
                else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) { onStart(); recording = true }
                else permission.launch(Manifest.permission.RECORD_AUDIO)
            } catch (error: Exception) { Toast.makeText(context, error.message ?: "recording failed", Toast.LENGTH_SHORT).show() }
        }) {
            Text(if (recording) "Stop recording" else "Start recording")
        }
        HorizontalDivider()
        Text("Local PCM enhancement preview", style = MaterialTheme.typography.titleMedium)
        Text("Captures 16 kHz mono PCM. The original is preserved, and a separate classical frame-gate derivative stays on this device. This preview is not a neural denoiser and does not auto-upload.")
        Text("Status: $pcmState")
        if (pcmPermissionDenied) Text("Microphone permission is required for the PCM preview.", color = MaterialTheme.colorScheme.error)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = pcmCapture != null && pcmState !in setOf("STARTING", "RECORDING", "FINALIZING"), onClick = {
                if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) pcmCapture?.start()
                else pcmPermission.launch(Manifest.permission.RECORD_AUDIO)
            }) { Text("Start PCM preview") }
            Button(enabled = pcmState == "RECORDING", onClick = { pcmCapture?.stop() }) { Text("Stop preview") }
        }
        pcmTakes.forEach { take ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("${take.status} · ${take.id.take(8)} · ${take.sampleCount / 16000.0}s")
                    take.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(enabled = File(take.originalPath).isFile, onClick = { pcmCapture?.play(take.originalPath) }) { Text("Play original") }
                        TextButton(enabled = File(take.enhancedPath).isFile, onClick = { pcmCapture?.play(take.enhancedPath) }) { Text("Play enhanced") }
                        TextButton(onClick = { pcmCapture?.delete(take.id) }) { Text("Delete both") }
                    }
                }
            }
        }
        Text("Offline history", style = MaterialTheme.typography.titleMedium)
        recordings.forEach { item ->
            RecordingRow(item, database, scope)
        }
    }
}

@Composable
private fun RecordingRow(item: RecordingEntity, database: RecordingDatabase?, scope: kotlinx.coroutines.CoroutineScope) {
    val context = LocalContext.current
    var text by remember(item.id, item.pendingEdit, item.transcript) { mutableStateOf(item.pendingEdit ?: item.transcript ?: "") }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${item.state} · ${item.id.take(8)}")
            item.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (item.transcript != null || item.pendingEdit != null) {
                OutlinedTextField(value = text, onValueChange = { text = it }, label = { Text("Review transcript") }, modifier = Modifier.fillMaxWidth())
                Button(enabled = item.serverRevision != null && text.isNotBlank(), onClick = {
                    scope.launch {
                        val operation = database?.recordings()?.nextOperation() ?: return@launch
                        database.recordings().edit(item.id, text, operation, System.currentTimeMillis())
                        UploadWorker.request(context, item.id, "${item.id}:sync:$operation")
                    }
                }) { Text("Save edit") }
            } else if (item.state == "INTERRUPTED") {
                Text("Recording was interrupted. The partial file remains offline and is not queued for upload.")
            }
            if (!item.deleted && database != null) {
                TextButton(onClick = {
                    scope.launch {
                        val operation = database.recordings().nextOperation()
                        database.recordings().delete(item.id, operation, System.currentTimeMillis())
                        UploadWorker.request(context, item.id, "${item.id}:sync:$operation")
                    }
                }) { Text("Delete") }
            }
        }
    }
}

internal suspend fun resumePendingSync(context: android.content.Context, database: RecordingDatabase) {
    val resumableStates = setOf("LOCAL_READY", "UPLOADING", "UPLOAD_RETRY", "UPLOADED", "PROCESSING", "EDITED", "DELETE_PENDING")
    database.recordings().resumableIds().forEach { id ->
        val pending = database.recordings().find(id) ?: return@forEach
        if (pending.state in resumableStates) {
            UploadWorker.request(context, id, "${id}:sync:${pending.localOperationId}")
        }
    }
}

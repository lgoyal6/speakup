package com.speakup

import android.Manifest
import android.os.Bundle
import android.widget.Toast
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import com.speakup.recording.AudioRecorder
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** UI state is ephemeral; Room/WorkManager own the durable recording and sync state. */
class MainActivity : ComponentActivity() {
    private lateinit var recorder: AudioRecorder
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState); recorder = AudioRecorder(this)
        setContent { SpeakUpScreen(onStart = { recorder.start() }, onStop = { recorder.stop() }) }
    }
    override fun onDestroy() { recorder.release(); super.onDestroy() }
}

@Composable
fun SpeakUpScreen(onStart: () -> Unit = {}, onStop: () -> Unit = {}) {
    val context = LocalContext.current
    var recording by remember { mutableStateOf(false) }
    var permissionDenied by remember { mutableStateOf(false) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionDenied = !granted
        if (granted) try { onStart(); recording = true } catch (error: Exception) { Toast.makeText(context, error.message ?: "recording failed", Toast.LENGTH_SHORT).show() }
    }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("SpeakUp", style = MaterialTheme.typography.headlineMedium)
        Text("Record offline, review the transcript, then export.")
        if (permissionDenied) Text("Microphone permission is required to record.", color = MaterialTheme.colorScheme.error)
        Button(onClick = {
            try {
                if (recording) { onStop(); recording = false }
                else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) { onStart(); recording = true }
                else permission.launch(Manifest.permission.RECORD_AUDIO)
            } catch (error: Exception) { Toast.makeText(context, error.message ?: "recording failed", Toast.LENGTH_SHORT).show() }
        }) {
            Text(if (recording) "Stop recording" else "Start recording")
        }
        OutlinedButton(onClick = { /* Room history screen lands here in the next slice. */ }) { Text("Offline history") }
    }
}

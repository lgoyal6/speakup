package com.speakup

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** UI state is ephemeral; Room/WorkManager own the durable recording and sync state. */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) { super.onCreate(savedInstanceState); setContent { SpeakUpScreen() } }
}

@Composable
fun SpeakUpScreen() {
    var recording by remember { mutableStateOf(false) }
    var permissionDenied by remember { mutableStateOf(false) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted -> permissionDenied = !granted }
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("SpeakUp", style = MaterialTheme.typography.headlineMedium)
        Text("Record offline, review the transcript, then export.")
        if (permissionDenied) Text("Microphone permission is required to record.", color = MaterialTheme.colorScheme.error)
        Button(onClick = { if (!recording) permission.launch(Manifest.permission.RECORD_AUDIO); recording = !recording }) {
            Text(if (recording) "Stop recording" else "Start recording")
        }
        OutlinedButton(onClick = { /* Room history screen lands here in the next slice. */ }) { Text("Offline history") }
    }
}

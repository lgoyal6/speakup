package com.speakup.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.speakup.BuildConfig
import java.net.URI
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Only ciphertext is stored in preferences. The key never leaves Android Keystore. */
class Credentials(context: Context) {
    private val preferences = context.getSharedPreferences("connection", Context.MODE_PRIVATE)
    data class Connection(val baseUrl: String, val token: String)
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("speakup-token", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("speakup-token", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun save(baseUrl: String, token: String) {
        val uri = URI(baseUrl.trim().trimEnd('/'))
        val local = uri.host in setOf("10.0.2.2", "127.0.0.1", "localhost")
        require(uri.host != null && uri.userInfo == null && uri.query == null && uri.fragment == null && (uri.path.isNullOrEmpty() || uri.path == "/")) { "Use an API origin without a path or credentials" }
        require(uri.scheme == "https" || (BuildConfig.DEBUG && local && uri.scheme == "http")) { "HTTPS is required; debug builds allow local emulator HTTP" }
        require(token.isNotBlank() && token.length <= 4096 && '\n' !in token && '\r' !in token) { "A valid bearer token is required" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val payload = cipher.iv + cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        check(preferences.edit().putString("origin", uri.toString().trimEnd('/')).putString("token", Base64.encodeToString(payload, Base64.NO_WRAP)).commit()) { "Could not save connection" }
    }
    fun load(): Connection? {
        val origin = preferences.getString("origin", null) ?: return null
        val encoded = preferences.getString("token", null) ?: return null
        return try {
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            require(bytes.size > 12)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12))) }
            Connection(origin, String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8))
        } catch (_: Exception) { null }
    }
}

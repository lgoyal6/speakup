# Worker leases and transcription contract

The SQLite reference backend uses database-serialized job claims with expiring tokens.
A worker must present the current token to publish or fail an attempt. Publication saves
raw provider output, its input/output hashes, the recording state, audit and job success
in one transaction. Expired attempts and deleted recordings cannot publish. A later
provider revision does not replace a user edit as the current transcript.

Retryable failures use exponential delay and a three-attempt budget. Interrupted final
attempts become terminal after their leases expire. The continuous worker polls when
idle and handles SIGTERM/SIGINT. A fixture provider requires explicit injection or the
fixture environment flag. The real HTTP adapter follows the OpenAI-compatible multipart
file/model/response_format contract with bounded timeout and response bytes. HTTPS is
required except for loopback contract tests. Provider names come from configuration,
not untrusted response metadata. Model provenance is the requested model identifier;
immutable vendor model revisions are not guaranteed by this protocol.

This completes the backend lease and publication contract, not the Android sync workflow
or validation against a live paid speech service.

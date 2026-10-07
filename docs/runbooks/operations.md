# Operations runbook

Alert on queued job age, upload retry spikes, provider failures, sync conflicts, deletion age, and storage growth. Inspect the durable jobs table, retry only failed jobs with the same idempotency key, and preserve the original provider revision when a retry returns different text. A provider outage should leave recordings in `FAILED` or `RETRYING`, never silently discard audio.

# outlabs-taskq 0.1.0a39 release notes

## Forced shutdown retry accounting

Workers can opt into `fail_on_shutdown_deadline`. Normal graceful shutdown
continues to release claims without consuming the retry budget. When a handler
is forcibly cancelled after the configured soft-stop deadline, the opt-in path
records a retryable `worker_shutdown_deadline` failure instead. This bounds
retries when a provider call may have been accepted but its result is unknown,
while preserving the existing release contract for ordinary drains.

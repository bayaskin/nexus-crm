const endpoints = new Set(["login", "me"]);
const outcomes = new Set(["completed", "failed"]);

// Serialize only the explicitly approved diagnostic fields. Callers may pass
// additional data, but it can never reach the console through this function.
export function serializeAuthDiagnostic(event) {
  if (!event || !endpoints.has(event.endpoint)) {
    throw new Error("Invalid auth diagnostic endpoint");
  }
  if (!outcomes.has(event.outcome)) {
    throw new Error("Invalid auth diagnostic outcome");
  }
  if (
    event.status !== null &&
    (!Number.isInteger(event.status) || event.status < 100 || event.status > 599)
  ) {
    throw new Error("Invalid auth diagnostic status");
  }
  if (
    event.elapsedMs !== null &&
    (!Number.isFinite(event.elapsedMs) || event.elapsedMs < 0)
  ) {
    throw new Error("Invalid auth diagnostic duration");
  }
  if (typeof event.navigationSucceeded !== "boolean") {
    throw new Error("Invalid auth diagnostic navigation result");
  }
  if (!Number.isInteger(event.workerIndex) || event.workerIndex < 0) {
    throw new Error("Invalid auth diagnostic worker index");
  }

  return JSON.stringify({
    endpoint: event.endpoint,
    status: event.status,
    elapsed_ms: event.elapsedMs === null ? null : Math.round(event.elapsedMs),
    outcome: event.outcome,
    navigation_to_orders_succeeded: event.navigationSucceeded,
    worker_index: event.workerIndex,
  });
}

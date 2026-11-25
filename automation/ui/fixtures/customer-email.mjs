import { randomUUID } from "node:crypto";

export function createCustomerEmail(workerIndex) {
  if (!Number.isSafeInteger(workerIndex) || workerIndex < 0) {
    throw new TypeError("Customer email requires a non-negative worker index");
  }
  return `playwright-${workerIndex}-${randomUUID()}@example.com`;
}

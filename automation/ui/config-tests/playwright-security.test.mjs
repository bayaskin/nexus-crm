import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { playwrightArtifactPolicy } from "../playwright-artifact-policy.mjs";
import { createCustomerEmail } from "../fixtures/customer-email.mjs";
import { serializeAuthDiagnostic } from "../fixtures/auth-diagnostics.mjs";

const config = await readFile(new URL("../playwright.config.ts", import.meta.url), "utf8");
const compose = await readFile(new URL("../../../docker-compose.qa.yml", import.meta.url), "utf8");
const guard = await readFile(new URL("../../../infra/qa/qa-guard.sh", import.meta.url), "utf8");

test("QA mode disables traces, screenshots, and video", () => {
  assert.deepEqual(playwrightArtifactPolicy("true"), {
    trace: "off",
    screenshot: "off",
    video: "off",
  });
  assert.match(config, /playwrightArtifactPolicy\(process\.env\.NEXUS_QA_MODE\)/);
  assert.match(config, /reporter:\s*qaMode\s*\?\s*\[\['list'\]\]/);
  assert.match(config, /PLAYWRIGHT_NO_COPY_PROMPT\s*=\s*'1'/);
  assert.match(compose, /ui-tests:[\s\S]*?NEXUS_QA_MODE:\s*["']true["']/);
  assert.match(guard, /\[ "\$\{NEXUS_QA_MODE:-\}" = "true" \]/);
});

test("auth diagnostics serialize only sanitized status and timing fields", () => {
  const line = serializeAuthDiagnostic({
    endpoint: "login",
    status: 401,
    elapsedMs: 42.4,
    outcome: "completed",
    navigationSucceeded: false,
    workerIndex: 3,
    password: "must-not-appear",
    token: "must-not-appear",
    cookie: "must-not-appear",
    body: "must-not-appear",
    url: "https://invalid.example/path?secret=must-not-appear",
    headers: { authorization: "must-not-appear" },
  });

  assert.deepEqual(JSON.parse(line), {
    endpoint: "login",
    status: 401,
    elapsed_ms: 42,
    outcome: "completed",
    navigation_to_orders_succeeded: false,
    worker_index: 3,
  });
  assert.doesNotMatch(line, /must-not-appear|secret|cookie|authorization|body|url/i);
  assert.throws(() => serializeAuthDiagnostic({ endpoint: "https://x/login" }));
});

test("auth diagnostics represent requests without a terminal event as unmeasured", () => {
  const diagnostic = JSON.parse(serializeAuthDiagnostic({
    endpoint: "me",
    status: null,
    elapsedMs: null,
    outcome: "failed",
    navigationSucceeded: false,
    workerIndex: 0,
  }));
  assert.equal(diagnostic.elapsed_ms, null);
  assert.equal(diagnostic.outcome, "failed");
});

test("QA mode suppresses Playwright's automatic DOM snapshot capture", () => {
  assert.match(config, /if \(qaMode\)[\s\S]*?PLAYWRIGHT_NO_COPY_PROMPT\s*=\s*'1'/);
  assert.match(config, /playwrightArtifactPolicy\(process\.env\.NEXUS_QA_MODE\)/);
  assert.match(config, /reporter:\s*qaMode\s*\?\s*\[\['list'\]\]/);
});

test("missing or false QA mode preserves local artifact behavior", () => {
  const localPolicy = { trace: "retain-on-failure", screenshot: "only-on-failure" };
  assert.deepEqual(playwrightArtifactPolicy(undefined), localPolicy);
  assert.deepEqual(playwrightArtifactPolicy("false"), localPolicy);
});

test("unrecognized QA mode fails closed instead of enabling failure traces", () => {
  for (const mode of ["1", "yes", "TRUE", "enabled"]) {
    assert.throws(() => playwrightArtifactPolicy(mode), /NEXUS_QA_MODE/);
  }
});

test("customer fixture emails include the worker index and a unique UUID", () => {
  const emailPattern = /^playwright-(\d+)-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@example\.com$/;
  const firstWorkerEmails = Array.from({ length: 10 }, () => createCustomerEmail(0));
  const secondWorkerEmail = createCustomerEmail(1);

  assert.equal(new Set(firstWorkerEmails).size, firstWorkerEmails.length);
  assert.notEqual(firstWorkerEmails[0], secondWorkerEmail);
  for (const email of [...firstWorkerEmails, secondWorkerEmail]) {
    assert.match(email, emailPattern);
  }
  assert.throws(() => createCustomerEmail(-1), /worker index/);
});

test("QA bootstrap always enters through its guarded fixed command", () => {
  assert.match(compose, /qa-bootstrap:[\s\S]*?entrypoint:\s*\["\/bin\/sh",\s*"\/qa\/qa-guard\.sh",\s*"bootstrap"\]/);
  assert.match(guard, /bootstrap\)[\s\S]*?exec python -m app\.commands\.bootstrap_admin/);
  const targetValidation = guard.indexOf("validate_database_target\n\ncase \"$mode\" in");
  const bootstrapMode = guard.indexOf("    bootstrap)");
  assert.ok(targetValidation >= 0 && bootstrapMode > targetValidation);
});

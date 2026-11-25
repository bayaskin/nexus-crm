import {
  test as base,
  expect,
  type Request,
  type Response,
} from "@playwright/test";
import { serializeAuthDiagnostic } from "./auth-diagnostics.mjs";

type Fixtures = {
  authenticatedPage: void;
};

export const test = base.extend<Fixtures>({
  authenticatedPage: async ({ page }, use, testInfo) => {
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;

    if (!email || !password) {
      throw new Error(
        "TEST_USER_EMAIL and TEST_USER_PASSWORD must be configured"
      );
    }

    const qaDiagnostics = process.env.NEXUS_QA_MODE === "true";
    const requests = new Map<
      Request,
      {
        endpoint: "login" | "me";
        startedAt: number;
        endedAt?: number;
        status: number | null;
        outcome: "completed" | "failed";
      }
    >();
    const endpointFor = (request: Request) => {
      let pathname;
      try {
        pathname = new URL(request.url()).pathname;
      } catch {
        return undefined;
      }
      if (/\/v1\/auth\/login\/?$/.test(pathname)) return "login";
      if (/\/v1\/auth\/me\/?$/.test(pathname)) return "me";
      return undefined;
    };
    const onRequest = (request: Request) => {
      if (!qaDiagnostics) return;
      const endpoint = endpointFor(request);
      if (endpoint) {
        requests.set(request, {
          endpoint,
          startedAt: performance.now(),
          status: null,
          outcome: "failed",
        });
      }
    };
    const onResponse = (response: Response) => {
      const diagnostic = requests.get(response.request());
      if (diagnostic) diagnostic.status = response.status();
    };
    const onRequestFinished = (request: Request) => {
      const diagnostic = requests.get(request);
      if (diagnostic) {
        diagnostic.outcome = "completed";
        diagnostic.endedAt = performance.now();
      }
    };
    const onRequestFailed = (request: Request) => {
      const diagnostic = requests.get(request);
      if (diagnostic) {
        diagnostic.outcome = "failed";
        diagnostic.endedAt = performance.now();
      }
    };
    let navigationSucceeded = false;

    if (qaDiagnostics) {
      page.on("request", onRequest);
      page.on("response", onResponse);
      page.on("requestfinished", onRequestFinished);
      page.on("requestfailed", onRequestFailed);
    }

    try {
      await page.goto("/");
      await page.getByLabel("Email address").fill(email);
      await page.getByLabel("Password").fill(password);
      await page.getByRole("button", { name: "Sign in" }).click();

      await expect(page).toHaveURL(/\/orders/);
      navigationSucceeded = true;
    } finally {
      if (qaDiagnostics) {
        page.off("request", onRequest);
        page.off("response", onResponse);
        page.off("requestfinished", onRequestFinished);
        page.off("requestfailed", onRequestFailed);
        for (const diagnostic of requests.values()) {
          console.info(
            `[qa-auth-diagnostic] ${serializeAuthDiagnostic({
              endpoint: diagnostic.endpoint,
              status: diagnostic.status,
              elapsedMs:
                diagnostic.endedAt === undefined
                  ? null
                  : diagnostic.endedAt - diagnostic.startedAt,
              outcome: diagnostic.outcome,
              navigationSucceeded,
              workerIndex: testInfo.workerIndex,
            })}`,
          );
        }
      }
    }

    await use();
  },
});

export { expect };

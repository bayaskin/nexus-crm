export type AuthDiagnostic = {
  endpoint: "login" | "me";
  status: number | null;
  elapsedMs: number | null;
  outcome: "completed" | "failed";
  navigationSucceeded: boolean;
  workerIndex: number;
};

export function serializeAuthDiagnostic(event: AuthDiagnostic): string;

export function playwrightArtifactPolicy(qaMode?: string): {
  trace: "off" | "retain-on-failure";
  screenshot: "off" | "only-on-failure";
  video?: "off";
};

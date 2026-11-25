const localArtifactPolicy = Object.freeze({
  trace: "retain-on-failure",
  screenshot: "only-on-failure",
});

const qaArtifactPolicy = Object.freeze({
  trace: "off",
  screenshot: "off",
  video: "off",
});

export function playwrightArtifactPolicy(qaMode) {
  if (qaMode === undefined || qaMode === "" || qaMode === "false") {
    return localArtifactPolicy;
  }
  if (qaMode === "true") {
    return qaArtifactPolicy;
  }
  throw new Error("NEXUS_QA_MODE must be exactly 'true' or 'false'");
}

import test from "node:test";
import assert from "node:assert/strict";
import worker, { CONSOLIDATED_CRON, SlotGate, dispatchAndVerify, failureDetails, observeSlot, planForCron, recheckRun, resolveScheduledPlan, recheckStartedRuns, slotKeyFor, verifyRunCompletion, waitForDispatchedRun } from "./worker.js";

const repo = "hanull01/naver-krx-universe-relay";
const finalPlan = planForCron("30 11 * * 1-5");
const finalTime = Date.UTC(2026, 9, 27, 11, 30);

function encoded(value) {
  return { content: Buffer.from(JSON.stringify(value), "utf8").toString("base64") };
}

function apiWith({ existing = null, run = null, commits = [], contents = {} } = {}) {
  const calls = [];
  let dispatched = false;
  const api = async (path) => {
    calls.push(path);
    if (path.includes("/dispatches")) { dispatched = true; return {}; }
    if (path.includes("/runs?event=schedule")) return { workflow_runs: existing ? [existing] : [] };
    if (path.includes("/runs?event=workflow_dispatch")) return { workflow_runs: dispatched && run ? [run] : [] };
    if (path.includes("/artifacts?")) return { artifacts: [] };
    if (path.includes("/actions/runs/")) return run || {};
    if (path.includes("/commits?")) return commits;
    if (path.includes("/contents/")) return contents[path.split("/contents/")[1]];
    throw new Error(`unexpected path ${path}`);
  };
  api.calls = calls;
  return api;
}

test("09:07 and 10:07 are independent slots", () => {
  const plan = planForCron("7 0-10 * * 1-5");
  const nine = slotKeyFor(plan, "7 0-10 * * 1-5", Date.UTC(2026, 9, 27, 0, 7));
  const ten = slotKeyFor(plan, "7 0-10 * * 1-5", Date.UTC(2026, 9, 27, 1, 7));
  assert.notEqual(nine, ten);
});

test("the consolidated Free-plan cron preserves all auxiliary KST slots", () => {
  const cron = "10,17,20,30,50 * * * 0-5";
  const at = (hour, minute) => Date.UTC(2026, 9, 27, hour - 9, minute);
  assert.equal(resolveScheduledPlan(cron, at(9, 17)).plan.key, "schedule-diagnostic");
  assert.equal(resolveScheduledPlan(cron, at(7, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(cron, at(12, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(cron, at(19, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(cron, at(8, 10)).plan.key, "snapshots");
  assert.equal(resolveScheduledPlan(cron, at(19, 50)).plan.key, "snapshots");
  assert.equal(resolveScheduledPlan(cron, at(9, 18)), null);
});

test("the consolidated cron covers Monday KST early slots and rejects weekend KST", () => {
  const cron = "10,17,20,30,50 * * * 0-5";
  const monday = (hour, minute) => Date.UTC(2026, 9, 26, hour - 9, minute);
  assert.equal(resolveScheduledPlan(cron, monday(7, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(cron, monday(8, 10)).plan.key, "snapshots");

  const sundayKst = Date.UTC(2026, 9, 24, 23, 20);
  assert.equal(resolveScheduledPlan(cron, sundayKst), null);
});

test("Durable Object claim is idempotent and records state", async () => {
  const values = new Map();
  const state = { storage: {
    get: async (key) => values.get(key),
    put: async (key, value) => values.set(key, value),
    list: async () => values,
  } };
  const gate = new SlotGate(state);
  const request = () => new Request("https://slot-gate/claim", { method: "POST", body: JSON.stringify({ slotKey: "refresh:2026-10-27T09:07" }) });
  assert.equal((await gate.fetch(request())).status, 201);
  assert.equal((await gate.fetch(request())).status, 409);
  const stateResponse = await gate.fetch(new Request("https://slot-gate/state", { method: "POST", body: JSON.stringify({ slotKey: "refresh:2026-10-27T09:07", status: "RUN_VERIFIED" }) }));
  assert.equal(stateResponse.status, 200);
  assert.equal(values.get("refresh:2026-10-27T09:07").status, "RUN_VERIFIED");
});

test("existing scheduled run blocks external dispatch", async () => {
  const scheduledTime = Date.now();
  const api = apiWith({ existing: { id: 100, event: "schedule", created_at: new Date(scheduledTime).toISOString() } });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, finalPlan, "refresh-final:slot", scheduledTime, { github: api, sleep: async () => {} });
  assert.equal(result.status, "EXISTING_RUN");
  assert.equal(api.calls.some((path) => path.includes("/dispatches")), false);
});

test("dispatch matches the newly created run and reports missing publication", async () => {
  const dispatchedRun = { id: 101, event: "workflow_dispatch", created_at: new Date(Date.now()).toISOString(), status: "completed", conclusion: "success", head_sha: "abc" };
  const api = apiWith({ run: dispatchedRun });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForCron("7 0-10 * * 1-5"), "refresh-intraday:slot", Date.now(), { github: api, sleep: async () => {} });
  assert.equal(result.status, "RUN_VERIFIED");
  assert.equal(result.publicationStatus, "PUBLISH_UNVERIFIED");
  assert.equal(result.runId, 101);
});

test("a run created before dispatch is not matched", async () => {
  const oldRun = { id: 104, event: "workflow_dispatch", created_at: new Date(Date.now() - 60_000).toISOString(), status: "completed", conclusion: "success", head_sha: "old" };
  const api = apiWith({ run: oldRun });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForCron("7 0-10 * * 1-5"), "refresh-intraday:old-run", Date.now(), { github: api, sleep: async () => {} });
  assert.equal(result.status, "RUN_UNVERIFIED");
});

test("GitHub dispatch errors are surfaced", async () => {
  const api = async (path) => {
    if (path.includes("/runs?event=schedule")) return { workflow_runs: [] };
    if (path.includes("/runs?event=workflow_dispatch")) return { workflow_runs: [] };
    if (path.includes("/dispatches")) throw new Error("GITHUB_HTTP_500");
    throw new Error(`unexpected path ${path}`);
  };
  await assert.rejects(
    dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForCron("7 0-10 * * 1-5"), "refresh-intraday:dispatch-error", Date.now(), { github: api, sleep: async () => {} }),
    /GITHUB_HTTP_500/,
  );
});

test("failure details classify GitHub HTTP errors without retaining messages", () => {
  for (const status of [401, 403, 404, 422, 429, 500]) {
    const error = new Error(`GITHUB_HTTP_${status}: bearer secret-value`);
    error.code = `GITHUB_HTTP_${status}`;
    error.httpStatus = status;
    error.stage = "POST_WORKFLOW_DISPATCH";
    const details = failureDetails(error);
    assert.deepEqual(details, {
      stage: "POST_WORKFLOW_DISPATCH",
      errorCode: `GITHUB_HTTP_${status}`,
      errorType: "GITHUB_API",
      httpStatus: status,
    });
    assert.equal(JSON.stringify(details).includes("secret-value"), false);
  }
});

test("Durable Object claim failures are classified separately", () => {
  const error = new Error("SLOT_STATE_HTTP_503");
  error.code = "SLOT_STATE_HTTP_503";
  error.httpStatus = 503;
  const details = failureDetails(error, "CLAIM_SLOT");
  assert.deepEqual(details, {
    stage: "CLAIM_SLOT",
    errorCode: "SLOT_STATE_HTTP_503",
    errorType: "DURABLE_OBJECT",
    httpStatus: 503,
  });
});

test("scheduled claim failure logs a sanitized stage and code", async () => {
  const logs = [];
  const originalLog = console.log;
  console.log = (value) => logs.push(String(value));
  try {
    const env = {
      GITHUB_REPOSITORY: repo,
      SCHEDULE_RECHECK_STARTED: "false",
      SCHEDULE_OBSERVE_ONLY: "false",
      SCHEDULE_RECHECK_ONLY: "false",
      SLOT_GATE: {
        idFromName: () => "scheduler",
        get: () => ({ fetch: async (request) => {
          const url = new URL(typeof request === "string" ? request : request.url);
          if (url.pathname.endsWith("/claim")) return new Response("unavailable", { status: 503 });
          if (url.pathname.endsWith("/state")) return new Response("updated", { status: 200 });
          throw new Error("unexpected Durable Object request");
        } }),
      },
    };
    let pending;
    const event = { cron: CONSOLIDATED_CRON, scheduledTime: Date.UTC(2026, 9, 27, 23, 30) };
    const ctx = { waitUntil: (promise) => { pending = promise; } };
    await worker.scheduled(event, env, ctx);
    await pending;
    const failure = logs.map((entry) => JSON.parse(entry)).find((entry) => entry.status === "DISPATCH_FAILED");
    assert.equal(failure.stage, "CLAIM_SLOT");
    assert.equal(failure.errorCode, "SLOT_STATE_HTTP_503");
    assert.equal(failure.errorType, "DURABLE_OBJECT");
    assert.equal("error" in failure, false);
  } finally {
    console.log = originalLog;
  }
});

test("FINAL publication checks all dates and diagnostic status", async () => {
  const now = new Date();
  const run = { id: 102, event: "workflow_dispatch", created_at: new Date(now.getTime() - 1000).toISOString(), updated_at: now.toISOString(), status: "completed", conclusion: "success", head_sha: "def", production_commit_sha: "pub123" };
  const commit = { sha: "pub123" };
  const common = { asOfDate: "20261027" };
  const contents = {
    "data/monitoring-daily/kospi.json?ref=pub123": encoded(common),
    "data/monitoring-daily/kosdaq.json?ref=pub123": encoded(common),
    "data/monitoring-baseline/latest.json?ref=pub123": encoded({ ...common, historyStatus: "SOURCE_OBSERVED" }),
    "data/monitoring-daily/trading-session-diagnostic.json?ref=pub123": encoded({ targetDate: "20261027", status: "VERIFIED" }),
  };
  const api = apiWith({ run, commits: [commit], contents });
  api.detail = true;
  const original = api;
  const wrapped = async (path, init) => {
    if (path === `/repos/${repo}/commits/pub123`) return { files: [{ filename: "data/monitoring/latest-quality.json" }], commit: { message: "Refresh NAVER market data", committer: { date: now.toISOString() } }, author: { login: "github-actions[bot]" } };
    return original(path, init);
  };
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, finalPlan, "refresh-final:slot", finalTime, { github: wrapped, sleep: async () => {} });
  assert.equal(result.status, "FINAL_PUBLICATION_VERIFIED");
  assert.equal(result.publicationSha, "pub123");
  assert.equal(api.calls.some((path) => path.includes("/contents/") && path.includes("ref=pub123")), true);
  assert.equal(api.calls.some((path) => path.includes("/contents/") && path.includes("ref=main")), false);
});

test("FINAL date mismatch is not accepted", async () => {
  const now = new Date();
  const run = { id: 103, event: "workflow_dispatch", created_at: new Date(now.getTime() - 1000).toISOString(), updated_at: now.toISOString(), status: "completed", conclusion: "success", head_sha: "ghi", production_commit_sha: "pub456" };
  const commit = { sha: "pub456" };
  const contents = {
    "data/monitoring-daily/kospi.json?ref=pub456": encoded({ asOfDate: "20261026" }),
    "data/monitoring-daily/kosdaq.json?ref=pub456": encoded({ asOfDate: "20261027" }),
    "data/monitoring-baseline/latest.json?ref=pub456": encoded({ asOfDate: "20261027", historyStatus: "SOURCE_OBSERVED" }),
    "data/monitoring-daily/trading-session-diagnostic.json?ref=pub456": encoded({ targetDate: "20261027", status: "VERIFIED" }),
  };
  const api = apiWith({ run, commits: [commit], contents });
  const wrapped = async (path, init) => path === `/repos/${repo}/commits/pub456`
    ? { files: [{ filename: "data/monitoring/latest-quality.json" }], commit: { message: "Refresh NAVER market data", committer: { date: now.toISOString() } }, author: { login: "github-actions[bot]" } } : api(path, init);
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, finalPlan, "refresh-final:slot", finalTime, { github: wrapped, sleep: async () => {} });
  assert.equal(result.status, "FINAL_DATE_MISMATCH");
});

test("a long-running Run returns its identity for later completion polling", async () => {
  const run = { id: 105, event: "workflow_dispatch", created_at: new Date().toISOString(), status: "in_progress", head_sha: "long" };
  const api = apiWith({ run });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForCron("7 0-10 * * 1-5"), "refresh-intraday:long", Date.now(), { github: api, sleep: async () => {} });
  assert.equal(result.status, "RUN_STARTED");
  assert.equal(result.runId, 105);
  const completed = { ...run, status: "completed", conclusion: "success", updated_at: new Date().toISOString() };
  const completionApi = async (path) => {
    if (path.includes("/actions/runs/105")) return completed;
    if (path.includes("/commits?")) return [];
    throw new Error(`unexpected path ${path}`);
  };
  const later = await verifyRunCompletion(completionApi, repo, planForCron("7 0-10 * * 1-5"), run);
  assert.equal(later.status, "RUN_VERIFIED");
  assert.equal(later.publicationStatus, "PUBLISH_UNVERIFIED");
});

test("recheckRun restores RUN_STARTED state and invokes completion verification", async () => {
  const values = new Map([["refresh-final:2026-10-27T20:30:30 11 * * 1-5", {
    status: "RUN_STARTED",
    details: { runId: 109, runHead: "resume", expectedDate: "20261027" },
  }]]);
  const gate = new SlotGate({ storage: {
    get: async (key) => values.get(key),
    put: async (key, value) => values.set(key, value),
    list: async () => values,
  } });
  const env = {
    GITHUB_REPOSITORY: repo,
    SLOT_GATE: { idFromName: () => "id", get: () => ({ fetch: (request, init) => gate.fetch(request instanceof Request ? request : new Request(request, init)) }) },
  };
  const api = async (path) => {
    if (path.includes("/actions/runs/109")) return { id: 109, head_sha: "resume", status: "completed", conclusion: "failure" };
    throw new Error(`unexpected path ${path}`);
  };
  const result = await recheckRun(env, "refresh-final:2026-10-27T20:30:30 11 * * 1-5", finalPlan, { github: api });
  assert.equal(result.status, "RUN_FAILED");
  assert.equal(values.get("refresh-final:2026-10-27T20:30:30 11 * * 1-5").status, "RUN_FAILED");
});

test("recheckStartedRuns scans persisted RUN_STARTED slots after restart", async () => {
  const slotKey = "refresh-final:2026-10-27T20:30:30 11 * * 1-5";
  const values = new Map([[slotKey, { slotKey, status: "RUN_STARTED", details: { planKey: "refresh-final", runId: 111, runHead: "restart", expectedDate: "20261027" } }]]);
  const gate = new SlotGate({ storage: {
    get: async (key) => values.get(key),
    put: async (key, value) => values.set(key, value),
    list: async () => values,
  } });
  const env = {
    GITHUB_REPOSITORY: repo,
    SLOT_GATE: { idFromName: () => "scheduler", get: () => ({ fetch: (request, init) => gate.fetch(request instanceof Request ? request : new Request(request, init)) }) },
  };
  const api = async (path) => path.includes("/actions/runs/111")
    ? { id: 111, head_sha: "restart", status: "completed", conclusion: "failure" }
    : (() => { throw new Error(`unexpected path ${path}`); })();
  const result = await recheckStartedRuns(env, { github: api });
  assert.equal(result.length, 1);
  assert.equal(result[0].status, "RUN_FAILED");
  assert.equal(values.get(slotKey).status, "RUN_FAILED");
});

test("Run creation remains observable at the 60-second boundary", async () => {
  const run = { id: 107, event: "workflow_dispatch", created_at: new Date().toISOString(), status: "in_progress", head_sha: "boundary" };
  let polls = 0;
  let sleeps = 0;
  const api = async (path) => {
    if (path.includes("/runs?event=schedule")) return { workflow_runs: [] };
    if (path.includes("/runs?event=workflow_dispatch")) return { workflow_runs: polls++ >= 11 ? [run] : [] };
    if (path.includes("/actions/runs/107")) return run;
    throw new Error(`unexpected path ${path}`);
  };
  const result = await waitForDispatchedRun(api, repo, planForCron("7 0-10 * * 1-5"), Date.now(), async () => { sleeps += 1; });
  assert.equal(sleeps, 12);
  assert.equal(result.status, "RUN_STARTED");
  assert.equal(result.run.id, 107);
});

test("a publication commit from another workflow is not selected", async () => {
  const now = new Date();
  const run = { id: 108, event: "workflow_dispatch", created_at: new Date(now.getTime() - 1000).toISOString(), updated_at: now.toISOString(), status: "completed", conclusion: "success", head_sha: "other" };
  const commit = { sha: "other-pub" };
  const api = apiWith({ run, commits: [commit] });
  const wrapped = async (path, init) => path === `/repos/${repo}/commits/other-pub`
    ? { files: [{ filename: "data/monitoring/latest-quality.json" }], commit: { message: "Update daily market report", committer: { date: now.toISOString() } }, author: { login: "github-actions[bot]" } } : api(path, init);
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, finalPlan, "refresh-final:other-publication", Date.now(), { github: wrapped, sleep: async () => {} });
  assert.equal(result.status, "RUN_VERIFIED");
  assert.equal(result.publicationStatus, "PUBLISH_UNVERIFIED");
});

test("the Run provenance artifact directly supplies its publication SHA", async () => {
  const now = new Date();
  const run = { id: 112, event: "workflow_dispatch", created_at: new Date(now.getTime() - 1000).toISOString(), updated_at: now.toISOString(), status: "completed", conclusion: "success", head_sha: "artifact-run" };
  const api = apiWith({ run });
  const wrapped = async (path, init) => {
    if (path.includes("/artifacts?")) return { artifacts: [{ name: "production-publication-112-artifactsha-true" }] };
    if (path === `/repos/${repo}/commits/artifactsha`) return { files: [
      { filename: "data/reports/latest.json" },
      { filename: "data/reports/latest.md" },
      { filename: "data/reports/latest.html" },
    ], commit: { message: "Update daily market report", committer: { date: now.toISOString() } }, author: { login: "github-actions[bot]" } };
    return api(path, init);
  };
  const result = await verifyRunCompletion(wrapped, repo, planForCron("0 14 * * 1-5"), run);
  assert.equal(result.publicationSha, "artifactsha");
  assert.equal(result.publicationStatus, "PUBLISHED");
});

test("daily report publication is unverified when the official HTML is missing", async () => {
  const now = new Date();
  const run = { id: 113, event: "workflow_dispatch", created_at: new Date(now.getTime() - 1000).toISOString(), updated_at: now.toISOString(), status: "completed", conclusion: "success", head_sha: "missing-html" };
  const api = apiWith({ run });
  const wrapped = async (path, init) => {
    if (path.includes("/artifacts?")) return { artifacts: [{ name: "production-publication-113-artifactsha-true" }] };
    if (path === `/repos/${repo}/commits/artifactsha`) return { files: [
      { filename: "data/reports/latest.json" },
      { filename: "data/reports/latest.md" },
    ], commit: { message: "Update daily market report", committer: { date: now.toISOString() } }, author: { login: "github-actions[bot]" } };
    return api(path, init);
  };
  const result = await verifyRunCompletion(wrapped, repo, planForCron("0 14 * * 1-5"), run);
  assert.equal(result.publicationStatus, "PUBLISH_UNVERIFIED");
  assert.equal(result.deliveryStatus, "UNVERIFIED");
});

test("cron observation mode never dispatches", async () => {
  const scheduledTime = Date.now();
  const api = apiWith({ existing: { id: 106, event: "schedule", created_at: new Date(scheduledTime).toISOString(), head_sha: "obs" } });
  const result = await observeSlot(api, repo, finalPlan, scheduledTime);
  assert.equal(result.status, "RUN_OBSERVED");
  assert.equal(result.observed, true);
  assert.equal(api.calls.some((path) => path.includes("/dispatches")), false);
});

test("research and snapshots keep execution times in their slot keys", () => {
  const research = planForCron("20 10 * * 1-5");
  const snapshots = planForCron("10,30,50 0-10 * * 1-5");
  assert.notEqual(slotKeyFor(research, "20 10 * * 1-5", Date.UTC(2026, 9, 27, 10, 20)), slotKeyFor(research, "20 10 * * 1-5", Date.UTC(2026, 9, 27, 11, 20)));
  assert.notEqual(slotKeyFor(snapshots, "10,30,50 0-10 * * 1-5", Date.UTC(2026, 9, 27, 1, 10)), slotKeyFor(snapshots, "10,30,50 0-10 * * 1-5", Date.UTC(2026, 9, 27, 1, 30)));
});

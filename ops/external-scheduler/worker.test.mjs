import test from "node:test";
import assert from "node:assert/strict";
import worker, { CONSOLIDATED_CRON, RESEARCH_CRON, SlotGate, dispatchAndVerify, failureDetails, observeSlot, planForCron, planForKey, recheckRun, resolveScheduledPlan, recheckStartedRuns, slotKeyFor, verifyRunCompletion, waitForDispatchedRun } from "./worker.js";

const repo = "hanull01/naver-krx-universe-relay";
const finalPlan = planForKey("refresh");

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

test("09:00 and 10:00 are independent slots", () => {
  const plan = planForKey("refresh");
  const nine = slotKeyFor(plan, CONSOLIDATED_CRON, Date.UTC(2026, 9, 27, 0, 0));
  const ten = slotKeyFor(plan, "0 0-10 * * 1-5", Date.UTC(2026, 9, 27, 1, 0));
  assert.notEqual(nine, ten);
});

test("the daily refresh cron dispatches the unified refresh at 08:30 KST", () => {
  const mondayRefresh = Date.UTC(2026, 9, 25, 23, 30);
  const refresh = resolveScheduledPlan(CONSOLIDATED_CRON, mondayRefresh);

  assert.equal(refresh.plan.workflow, "refresh.yml");
  assert.deepEqual(refresh.plan.inputs, {});
  assert.equal(refresh.plan.slot, "08:30-20:30");
  assert.equal(refresh.plan.key, "refresh");
});

test("the refresh cron maps UTC Sunday-Thursday to KST Monday-Friday and rejects weekends", () => {
  const mondayKst = Date.UTC(2026, 9, 25, 23, 30);
  const sundayKst = Date.UTC(2026, 9, 24, 23, 30);
  assert.equal(resolveScheduledPlan(CONSOLIDATED_CRON, mondayKst).plan.key, "refresh");
  assert.equal(resolveScheduledPlan(CONSOLIDATED_CRON, sundayKst), null);
});

test("the canonical cron maps only refresh and research slots", () => {
  assert.equal(planForCron(CONSOLIDATED_CRON).key, "refresh");
  assert.equal(planForCron(RESEARCH_CRON).key, "research");
  assert.equal(planForCron("30 0-11 * * MON-FRI"), null);
  assert.equal(planForCron("20 10 * * MON-FRI"), null);
  assert.equal(planForCron("0 22 * * SUN-THU"), null);
  assert.equal(planForCron("0 23 * * SUN-THU"), null);
  assert.equal(planForCron("0 14 * * MON-FRI"), null);
  assert.equal(planForCron("10,17,20,30,50 * * * 0-5"), null);
});

test("the research cron dispatches only the three approved KST times", () => {
  assert.equal(resolveScheduledPlan(RESEARCH_CRON, Date.UTC(2026, 9, 25, 22, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(RESEARCH_CRON, Date.UTC(2026, 9, 26, 3, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(RESEARCH_CRON, Date.UTC(2026, 9, 26, 10, 20)).plan.key, "research");
  assert.equal(resolveScheduledPlan(RESEARCH_CRON, Date.UTC(2026, 9, 26, 0, 20)), null);
});

test("backfill publication verification accepts the workflow's dated commit message", async () => {
  const now = new Date();
  const run = {
    id: 114,
    event: "workflow_dispatch",
    created_at: new Date(now.getTime() - 1000).toISOString(),
    updated_at: now.toISOString(),
    status: "completed",
    conclusion: "success",
    head_sha: "backfill-run",
    production_commit_sha: "backfill-pub",
  };
  const plan = planForKey("backfill-derived");
  const api = async (path) => {
    if (path === `/repos/${repo}/actions/runs/114`) return run;
    if (path === `/repos/${repo}/commits/backfill-pub`) return {
      files: [{ filename: "data/monitoring-derived/20261030/114/manifest.json" }],
      commit: {
        message: "Publish full-market derived data 2026-10-30",
        committer: { date: now.toISOString() },
      },
      author: { login: "github-actions[bot]" },
    };
    throw new Error(`unexpected path ${path}`);
  };
  const result = await verifyRunCompletion(api, repo, plan, run);
  assert.equal(result.status, "RUN_VERIFIED");
  assert.equal(result.publicationSha, "backfill-pub");
  assert.equal(result.publicationStatus, "PUBLISHED");
});

test("the legacy consolidated cron is ignored", () => {
  const cron = "10,17,20,30,50 * * * 0-5";
  const at = (hour, minute) => Date.UTC(2026, 9, 27, hour - 9, minute);
  for (const [hour, minute] of [[7, 20], [8, 10], [8, 30], [9, 17], [12, 20], [19, 20], [19, 50]]) {
    assert.equal(resolveScheduledPlan(cron, at(hour, minute)), null);
  }
});

test("Durable Object claim is idempotent and records state", async () => {
  const values = new Map();
  const state = { storage: {
    get: async (key) => values.get(key),
    put: async (key, value) => values.set(key, value),
    list: async () => values,
  } };
  const gate = new SlotGate(state);
  const request = () => new Request("https://slot-gate/claim", { method: "POST", body: JSON.stringify({ slotKey: "refresh:2026-10-27T09:00" }) });
  assert.equal((await gate.fetch(request())).status, 201);
  assert.equal((await gate.fetch(request())).status, 409);
  const stateResponse = await gate.fetch(new Request("https://slot-gate/state", { method: "POST", body: JSON.stringify({ slotKey: "refresh:2026-10-27T09:00", status: "RUN_VERIFIED" }) }));
  assert.equal(stateResponse.status, 200);
  assert.equal(values.get("refresh:2026-10-27T09:00").status, "RUN_VERIFIED");
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
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForKey("refresh"), "refresh-intraday:slot", Date.now(), { github: api, sleep: async () => {} });
  assert.equal(result.status, "RUN_VERIFIED");
  assert.equal(result.publicationStatus, "PUBLISH_UNVERIFIED");
  assert.equal(result.runId, 101);
});

test("a run created before dispatch is not matched", async () => {
  const oldRun = { id: 104, event: "workflow_dispatch", created_at: new Date(Date.now() - 60_000).toISOString(), status: "completed", conclusion: "success", head_sha: "old" };
  const api = apiWith({ run: oldRun });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForKey("refresh"), "refresh-intraday:old-run", Date.now(), { github: api, sleep: async () => {} });
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
    dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForKey("refresh"), "refresh-intraday:dispatch-error", Date.now(), { github: api, sleep: async () => {} }),
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

test("a long-running Run returns its identity for later completion polling", async () => {
  const run = { id: 105, event: "workflow_dispatch", created_at: new Date().toISOString(), status: "in_progress", head_sha: "long" };
  const api = apiWith({ run });
  const result = await dispatchAndVerify({ GITHUB_REPOSITORY: repo }, planForKey("refresh"), "refresh-intraday:long", Date.now(), { github: api, sleep: async () => {} });
  assert.equal(result.status, "RUN_STARTED");
  assert.equal(result.runId, 105);
  const completed = { ...run, status: "completed", conclusion: "success", updated_at: new Date().toISOString() };
  const completionApi = async (path) => {
    if (path.includes("/actions/runs/105")) return completed;
    if (path.includes("/commits?")) return [];
    throw new Error(`unexpected path ${path}`);
  };
  const later = await verifyRunCompletion(completionApi, repo, planForKey("refresh"), run);
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
  const result = await waitForDispatchedRun(api, repo, planForKey("refresh"), Date.now(), async () => { sleeps += 1; });
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
  const result = await verifyRunCompletion(wrapped, repo, planForKey("daily-report"), run);
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
  const result = await verifyRunCompletion(wrapped, repo, planForKey("daily-report"), run);
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

test("research and refresh keep execution times in their slot keys", () => {
  const research = planForKey("research");
  const refresh = planForKey("refresh");
  assert.notEqual(slotKeyFor(research, RESEARCH_CRON, Date.UTC(2026, 9, 27, 10, 20)), slotKeyFor(research, RESEARCH_CRON, Date.UTC(2026, 9, 27, 11, 20)));
  assert.notEqual(slotKeyFor(refresh, CONSOLIDATED_CRON, Date.UTC(2026, 9, 27, 1, 30)), slotKeyFor(refresh, CONSOLIDATED_CRON, Date.UTC(2026, 9, 27, 2, 30)));
});


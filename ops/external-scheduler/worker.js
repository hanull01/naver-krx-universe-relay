const WORKFLOWS = {
  refresh: { workflow: "refresh.yml", publicationPaths: ["data/monitoring/latest-quality.json"], commitMessage: "Refresh NAVER market data" },
  report: { workflow: "daily-report.yml", publicationPaths: ["data/reports/latest.json"], commitMessage: "Update daily market report" },
  research: { workflow: "refresh-research.yml", publicationPaths: ["data/research/latest.json"], commitMessage: "Update NAVER research intelligence" },
  snapshots: { workflow: "refresh-market-snapshots.yml", publicationPaths: ["data/market"], commitMessage: "Refresh NAVER market snapshots" },
  diagnostic: { workflow: "schedule-diagnostic.yml", publicationPaths: [] },
};

export const SLOT_GRACE_BEFORE_MS = 5 * 60 * 1000;
export const SLOT_GRACE_AFTER_MS = 15 * 60 * 1000;
export const CONSOLIDATED_CRON = "10,17,20,30,50 * * * SUN-FRI";
export const GITHUB_USER_AGENT = "naver-krx-external-scheduler";
const LEGACY_CONSOLIDATED_CRON = "10,17,20,30,50 * * * 0-5";

export function planForCron(cron) {
  if (cron === "30 11 * * MON-FRI" || cron === "30 11 * * 1-5") {
    return { slot: "20:30", key: "refresh-final", ...WORKFLOWS.refresh, inputs: { run_mode: "final_recovery" } };
  }
  if (cron === "0 23 * * SUN-THU" || cron === "0 23 * * 0-4" || cron === "7 0-10 * * MON-FRI" || cron === "7 0-10 * * 1-5") {
    return { slot: cron.startsWith("0 23") ? "08:00" : "09:07-19:07", key: "refresh-intraday", ...WORKFLOWS.refresh, inputs: { run_mode: "intraday" } };
  }
  if (cron === "0 14 * * MON-FRI" || cron === "0 14 * * 1-5") return { slot: "23:00", key: "daily-report", ...WORKFLOWS.report, inputs: {} };
  if (cron === "17 0 * * MON-FRI" || cron === "17 0 * * 1-5") return { slot: "09:17", key: "schedule-diagnostic", ...WORKFLOWS.diagnostic, inputs: {} };
  if (cron === "20 22 * * SUN-THU" || cron === "20 22 * * 0-4" || cron === "20 3 * * MON-FRI" || cron === "20 3 * * 1-5" || cron === "20 10 * * MON-FRI" || cron === "20 10 * * 1-5") {
    return { slot: "research", key: "research", ...WORKFLOWS.research, inputs: {} };
  }
  if (cron === "10,30,50 23 * * SUN-THU" || cron === "10,30,50 23 * * 0-4" || cron === "10,30,50 0-10 * * MON-FRI" || cron === "10,30,50 0-10 * * 1-5") {
    return { slot: "snapshots", key: "snapshots", ...WORKFLOWS.snapshots, inputs: {} };
  }
  return null;
}

export function planForKey(key) {
  const plans = {
    "refresh-final": { slot: "20:30", key, ...WORKFLOWS.refresh, inputs: { run_mode: "final_recovery" } },
    "refresh-intraday": { slot: "08:00-19:07", key, ...WORKFLOWS.refresh, inputs: { run_mode: "intraday" } },
    "daily-report": { slot: "23:00", key, ...WORKFLOWS.report, inputs: {} },
    "schedule-diagnostic": { slot: "09:17", key, ...WORKFLOWS.diagnostic, inputs: {} },
    research: { slot: "research", key, ...WORKFLOWS.research, inputs: {} },
    snapshots: { slot: "snapshots", key, ...WORKFLOWS.snapshots, inputs: {} },
  };
  return plans[key] || null;
}

function kstParts(scheduledTime) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", weekday: "short", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(scheduledTime));
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
}

export function slotKeyFor(plan, cron, scheduledTime) {
  const kst = kstParts(scheduledTime);
  return `${plan.key}:${kst.year}-${kst.month}-${kst.day}T${kst.hour}:${kst.minute}:${cron}`;
}

export function resolveScheduledPlan(cron, scheduledTime) {
  if (cron !== CONSOLIDATED_CRON && cron !== LEGACY_CONSOLIDATED_CRON) {
    const plan = planForCron(cron);
    return plan ? { plan, slotCron: cron } : null;
  }
  const kst = kstParts(scheduledTime);
  if (kst.weekday === "Sat" || kst.weekday === "Sun") return null;
  const hour = Number(kst.hour);
  const minute = Number(kst.minute);
  if (hour === 9 && minute === 17) return { plan: planForKey("schedule-diagnostic"), slotCron: "17 0 * * 1-5" };
  if (minute === 20 && [7, 12, 19].includes(hour)) return { plan: planForKey("research"), slotCron: "20 research" };
  if ([10, 30, 50].includes(minute) && hour >= 8 && hour <= 19) return { plan: planForKey("snapshots"), slotCron: "10,30,50 snapshots" };
  return null;
}

function headers(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "User-Agent": GITHUB_USER_AGENT,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

async function github(env, path, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: { ...headers(env), ...(init.headers || {}) },
  });
  const text = await response.text();
  let body;
  try { body = text ? JSON.parse(text) : {}; } catch { body = {}; }
  if (!response.ok) {
    const error = new Error(`GITHUB_HTTP_${response.status}`);
    error.code = `GITHUB_HTTP_${response.status}`;
    error.httpStatus = response.status;
    throw error;
  }
  return body;
}

function stageError(stage, error) {
  if (error && typeof error === "object") {
    if (!error.stage) error.stage = stage;
    return error;
  }
  const wrapped = new Error(String(error));
  wrapped.stage = stage;
  return wrapped;
}

async function atStage(stage, operation) {
  try {
    return await operation();
  } catch (error) {
    throw stageError(stage, error);
  }
}

export function failureDetails(error, fallbackStage = "UNKNOWN") {
  const message = String(error?.message || error || "");
  const code = String(error?.code || message.match(/(?:GITHUB|SLOT_STATE|SLOT_STARTED)_HTTP_\d+/)?.[0] || "WORKER_ERROR");
  const statusMatch = code.match(/(?:GITHUB|SLOT_STATE|SLOT_STARTED)_HTTP_(\d+)/);
  const httpStatus = error?.httpStatus || (statusMatch ? Number(statusMatch[1]) : undefined);
  const errorType = code.startsWith("GITHUB_HTTP_") ? "GITHUB_API"
    : (code.startsWith("SLOT_") ? "DURABLE_OBJECT" : "WORKER");
  return {
    stage: error?.stage || fallbackStage,
    errorCode: code,
    errorType,
    ...(httpStatus ? { httpStatus } : {}),
  };
}

async function listRuns(api, repository, workflow, event) {
  const result = await api(`/repos/${repository}/actions/workflows/${workflow}/runs?event=${event}&branch=main&per_page=50`);
  return result.workflow_runs || [];
}

export async function findExistingRun(api, repository, plan, scheduledTime) {
  const lower = scheduledTime - SLOT_GRACE_BEFORE_MS;
  const upper = scheduledTime + SLOT_GRACE_AFTER_MS;
  const runs = [
    ...(await listRuns(api, repository, plan.workflow, "schedule")),
    ...(await listRuns(api, repository, plan.workflow, "workflow_dispatch")),
  ];
  return runs.find((run) => {
    const created = Date.parse(run.created_at || "");
    return Number.isFinite(created) && created >= lower && created <= upper;
  }) || null;
}

export async function waitForDispatchedRun(api, repository, plan, dispatchedAt, sleep) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await sleep(5000);
    const runs = await listRuns(api, repository, plan.workflow, "workflow_dispatch");
    const run = runs
      .filter((item) => Date.parse(item.created_at || "") >= dispatchedAt - 5000)
      .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))[0];
    if (!run) continue;
    const current = await api(`/repos/${repository}/actions/runs/${run.id}`);
    if (current.status === "completed") return { status: "COMPLETED", run: current };
    return { status: "RUN_STARTED", run: current };
  }
  return { status: "RUN_UNVERIFIED", run: null };
}

async function commitChangedPaths(api, repository, commits, publicationPaths, run, commitMessage) {
  for (const commit of commits || []) {
    const detail = await api(`/repos/${repository}/commits/${commit.sha}`);
    const committedAt = Date.parse(detail.commit?.committer?.date || "");
    const runStarted = Date.parse(run.created_at || "");
    const runFinished = Date.parse(run.updated_at || run.created_at || "");
    const authorLogin = detail.author?.login || detail.commit?.author?.name || "";
    const message = (detail.commit?.message || "").split("\n", 1)[0];
    if (!Number.isFinite(committedAt) || committedAt < runStarted || committedAt > runFinished
        || !String(authorLogin).includes("github-actions") || message !== commitMessage) continue;
    const filenames = (detail.files || []).map((file) => file.filename || "");
    if (publicationPaths.some((path) => filenames.some((name) =>
      name === path || name.startsWith(`${path.replace(/\/$/, "")}/`)))) return commit.sha;
  }
  return null;
}

async function publicationFromArtifact(api, repository, run) {
  const result = await api(`/repos/${repository}/actions/runs/${run.id}/artifacts?per_page=100`);
  const prefix = `production-publication-${run.id}-`;
  const artifact = (result.artifacts || []).find((item) => item.name?.startsWith(prefix));
  if (!artifact) return null;
  const suffix = artifact.name.slice(prefix.length).split("-");
  return { sha: suffix[0], changed: suffix[1] === "true" };
}

export async function verifyRunCompletion(api, repository, plan, run, options = {}) {
  const current = await api(`/repos/${repository}/actions/runs/${run.id}`);
  if (current.status !== "completed") {
    return { runId: current.id, runHead: current.head_sha, status: "RUN_STARTED" };
  }
  const result = { runId: current.id, runHead: current.head_sha,
    status: current.conclusion === "success" ? "RUN_VERIFIED" : "RUN_FAILED" };
  if (result.status !== "RUN_VERIFIED") return result;
  const directSha = current.production_commit_sha || current.outputs?.production_commit_sha;
  const artifactPublication = directSha ? null : await publicationFromArtifact(api, repository, current);
  const publicationSha = directSha || artifactPublication?.sha;
  if (!publicationSha) {
    result.publicationStatus = "PUBLISH_UNVERIFIED";
    result.deliveryStatus = "UNVERIFIED";
    return result;
  }
  result.publicationSha = await commitChangedPaths(api, repository, [{ sha: publicationSha }], plan.publicationPaths, current, plan.commitMessage);
  result.publicationChanged = directSha ? current.publication_changed !== false : artifactPublication.changed;
  result.publicationStatus = result.publicationSha ? (result.publicationChanged ? "PUBLISHED" : "UNCHANGED") : "PUBLISH_UNVERIFIED";
  if (!result.publicationChanged) {
    result.deliveryStatus = "UNVERIFIED";
    return result;
  }
  if (plan.key === "refresh-final" && result.publicationSha) {
    const expectedDate = options.expectedDate;
    Object.assign(result, await verifyFinalPublication(api, repository, expectedDate, result.publicationSha));
  }
  result.deliveryStatus = "UNVERIFIED";
  return result;
}

async function readState(env, slotKey) {
  const stub = schedulerStub(env);
  const response = await stub.fetch("https://slot-gate/state?slotKey=" + encodeURIComponent(slotKey));
  if (response.status === 404) return null;
  if (!response.ok) {
    const error = new Error(`SLOT_STATE_HTTP_${response.status}`);
    error.code = `SLOT_STATE_HTTP_${response.status}`;
    error.httpStatus = response.status;
    throw error;
  }
  return response.json();
}

export async function recheckRun(env, slotKey, plan, options = {}) {
  const stored = await readState(env, slotKey);
  const details = stored?.details || {};
  if (!details.runId) return { slotKey, workflow: plan.workflow, status: "RUN_UNVERIFIED" };
  const api = options.github || ((path, init) => github(env, path, init));
  const result = await verifyRunCompletion(api, env.GITHUB_REPOSITORY, plan, {
    id: details.runId,
    head_sha: details.runHead,
    created_at: details.runCreatedAt,
    updated_at: details.runUpdatedAt,
  }, { expectedDate: details.expectedDate });
  const state = result.publicationStatus === "PUBLISH_UNVERIFIED"
    ? "RUN_STARTED"
    : (result.status === "FINAL_PUBLICATION_VERIFIED" || result.status === "PUBLISHED" ? "PUBLISHED" : result.status);
  await setState(env, slotKey, state, { ...details, ...result });
  return { slotKey, workflow: plan.workflow, ...result };
}

function schedulerStub(env) {
  const id = env.SLOT_GATE.idFromName("scheduler");
  return env.SLOT_GATE.get(id);
}

export async function recheckStartedRuns(env, options = {}) {
  const stub = schedulerStub(env);
  const response = await stub.fetch("https://slot-gate/started");
  if (!response.ok) throw new Error(`SLOT_STARTED_HTTP_${response.status}`);
  const entries = await response.json();
  const results = [];
  for (const entry of entries) {
    const plan = planForKey(entry.details?.planKey);
    if (!plan) {
      results.push({ slotKey: entry.slotKey, status: "RUN_UNVERIFIED" });
      continue;
    }
    results.push(await recheckRun(env, entry.slotKey, plan, options));
  }
  return results;
}

export async function observeSlot(api, repository, plan, scheduledTime) {
  const run = await findExistingRun(api, repository, plan, scheduledTime);
  if (!run) return { workflow: plan.workflow, status: "NO_RUN", observed: true };
  return { workflow: plan.workflow, status: "RUN_OBSERVED", observed: true,
    runId: run.id, runHead: run.head_sha, runEvent: run.event };
}

export async function verifyFinalPublication(api, repository, expectedDate, publicationSha) {
  const paths = [
    "data/monitoring-daily/kospi.json",
    "data/monitoring-daily/kosdaq.json",
    "data/monitoring-baseline/latest.json",
    "data/monitoring-daily/trading-session-diagnostic.json",
  ];
  const payloads = {};
  for (const path of paths) payloads[path] = await api(`/repos/${repository}/contents/${path}?ref=${encodeURIComponent(publicationSha)}`);
  const decode = (item) => JSON.parse(atob(item.content.replace(/\n/g, "")));
  const kospi = decode(payloads[paths[0]]);
  const kosdaq = decode(payloads[paths[1]]);
  const baseline = decode(payloads[paths[2]]);
  const diagnostic = decode(payloads[paths[3]]);
  const dates = [kospi.asOfDate, kosdaq.asOfDate, baseline.asOfDate, String(diagnostic.targetDate || "").replace(/-/g, "")];
  if (dates.some((date) => date !== expectedDate)) return { status: "FINAL_DATE_MISMATCH", publicationSha };
  if (diagnostic.status !== "VERIFIED") return { status: "FINAL_DIAGNOSTIC_UNVERIFIED", publicationSha };
  if (baseline.historyStatus !== "SOURCE_OBSERVED" && baseline.historyStatus !== "OK") return { status: "FINAL_BASELINE_INVALID", publicationSha };
  return { status: "FINAL_PUBLICATION_VERIFIED", publicationSha };
}

export async function dispatchAndVerify(env, plan, slotKey, scheduledTime, options = {}) {
  const repository = env.GITHUB_REPOSITORY;
  const api = options.github || ((path, init) => github(env, path, init));
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const existing = await atStage("FIND_EXISTING_RUN", () => findExistingRun(api, repository, plan, scheduledTime));
  if (existing) return { slotKey, workflow: plan.workflow, status: "EXISTING_RUN", runId: existing.id, runHead: existing.head_sha, runEvent: existing.event };

  const dispatchedAt = Date.now();
  await atStage("POST_WORKFLOW_DISPATCH", () => api(`/repos/${repository}/actions/workflows/${plan.workflow}/dispatches`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: plan.inputs }),
  }));
  const creation = await atStage("WAIT_FOR_GITHUB_RUN", () => waitForDispatchedRun(api, repository, plan, dispatchedAt, sleep));
  if (!creation.run) return { slotKey, workflow: plan.workflow, status: "RUN_UNVERIFIED" };
  const kst = kstParts(scheduledTime);
  const expectedDate = `${kst.year}${kst.month}${kst.day}`;
  const result = { slotKey, planKey: plan.key, workflow: plan.workflow, runId: creation.run.id, runHead: creation.run.head_sha,
    runCreatedAt: creation.run.created_at, runUpdatedAt: creation.run.updated_at,
    expectedDate, status: creation.status };
  if (creation.status !== "COMPLETED") return result;
  return { ...result, ...(await atStage("VERIFY_RUN", () => verifyRunCompletion(api, repository, plan, creation.run, { expectedDate }))) };
}

async function setState(env, slotKey, status, details = {}) {
  const stub = schedulerStub(env);
  const response = await stub.fetch("https://slot-gate/state", { method: "POST", body: JSON.stringify({ slotKey, status, details }) });
  if (!response.ok) {
    const error = new Error(`SLOT_STATE_HTTP_${response.status}`);
    error.code = `SLOT_STATE_HTTP_${response.status}`;
    error.httpStatus = response.status;
    throw error;
  }
}

async function claim(env, slotKey) {
  const stub = schedulerStub(env);
  const response = await stub.fetch("https://slot-gate/claim", { method: "POST", body: JSON.stringify({ slotKey }) });
  if (response.status === 201) return true;
  if (response.status === 409) return false;
  const error = new Error(`SLOT_STATE_HTTP_${response.status}`);
  error.code = `SLOT_STATE_HTTP_${response.status}`;
  error.httpStatus = response.status;
  throw error;
}

export default {
  async scheduled(event, env, ctx) {
    const resolved = resolveScheduledPlan(event.cron, event.scheduledTime);
    if (!resolved) return;
    const { plan, slotCron } = resolved;
    const slotKey = slotKeyFor(plan, slotCron, event.scheduledTime);
    ctx.waitUntil((async () => {
      let stage = "RECHECK_STARTED";
      try {
        if (env.SCHEDULE_RECHECK_STARTED === "true") {
          const rechecked = await atStage("RECHECK_STARTED", () => recheckStartedRuns(env));
          if (rechecked.length) console.log(JSON.stringify({ status: "STARTED_RUNS_RECHECKED", results: rechecked }));
        }
        if (env.SCHEDULE_OBSERVE_ONLY === "true") {
          const result = await atStage("FIND_EXISTING_RUN", () => observeSlot((path, init) => github(env, path, init), env.GITHUB_REPOSITORY, plan, event.scheduledTime));
          console.log(JSON.stringify({ slotKey, ...result }));
          return;
        }
        if (env.SCHEDULE_RECHECK_ONLY === "true") {
          const result = await atStage("RECHECK_STARTED", () => recheckStartedRuns(env));
          console.log(JSON.stringify({ slotKey, status: "RECHECKED", results: result }));
          return;
        }
        stage = "CLAIM_SLOT";
        if (!(await claim(env, slotKey))) return;
        stage = "SET_STATE";
        await setState(env, slotKey, "CLAIMED");
        stage = "SET_STATE";
        await setState(env, slotKey, "DISPATCHED");
        const runResult = await dispatchAndVerify(env, plan, slotKey, event.scheduledTime);
        runResult.deliveryStatus = "UNVERIFIED";
        const result = { ...runResult, scheduledTime: event.scheduledTime };
        const state = result.publicationStatus === "PUBLISH_UNVERIFIED"
          ? "RUN_STARTED"
          : (result.status === "FINAL_PUBLICATION_VERIFIED" || result.status === "PUBLISHED" ? "PUBLISHED" : result.status);
        stage = "SAVE_RESULT";
        await setState(env, slotKey, state, result);
        console.log(JSON.stringify(result));
      } catch (error) {
        const details = failureDetails(error, stage);
        try {
          await setState(env, slotKey, "FAILED", details);
        } catch (stateError) {
          console.log(JSON.stringify({ slotKey, workflow: plan.workflow, scheduledTime: event.scheduledTime,
            stage: "SET_STATE", ...failureDetails(stateError, "SET_STATE"), status: "DISPATCH_FAILED" }));
          return;
        }
        console.log(JSON.stringify({ slotKey, workflow: plan.workflow, scheduledTime: event.scheduledTime,
          workerVersion: env.WORKER_VERSION || null, status: "DISPATCH_FAILED", ...details }));
      }
    })());
  },
};

export class SlotGate {
  constructor(state) { this.state = state; }

  async fetch(request) {
    if (request.method === "GET") {
      const url = new URL(request.url);
      if (url.pathname.endsWith("/started")) {
        const entries = [];
        for (const [slotKey, value] of await this.state.storage.list()) {
          if (value?.status === "RUN_STARTED") entries.push({ slotKey, ...value });
        }
        return Response.json(entries);
      }
      const slotKey = url.searchParams.get("slotKey");
      if (!slotKey) return new Response("slot required", { status: 400 });
      const current = await this.state.storage.get(slotKey);
      return current ? Response.json(current) : new Response("not found", { status: 404 });
    }
    if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
    const body = await request.json();
    if (!body.slotKey) return new Response("slot required", { status: 400 });
    if (body.status) {
      const current = await this.state.storage.get(body.slotKey);
      if (!current) return new Response("not claimed", { status: 404 });
      await this.state.storage.put(body.slotKey, { ...current, status: body.status, details: body.details || {}, updatedAt: new Date().toISOString() }, { expirationTtl: 172800 });
      return new Response("updated", { status: 200 });
    }
    if (await this.state.storage.get(body.slotKey)) return new Response("duplicate", { status: 409 });
    await this.state.storage.put(body.slotKey, { slotKey: body.slotKey, status: "CLAIMED", claimedAt: new Date().toISOString() }, { expirationTtl: 172800 });
    return new Response("claimed", { status: 201 });
  }
}

const WORKFLOWS = {
  refresh: { workflow: "refresh.yml", publicationPaths: ["data/monitoring/latest-quality.json"], commitMessage: "Refresh NAVER market data" },
  report: { workflow: "daily-report.yml", publicationPaths: ["data/reports/latest.json"], commitMessage: "Update daily market report" },
  research: { workflow: "refresh-research.yml", publicationPaths: ["data/research/latest.json"], commitMessage: "Update NAVER research intelligence" },
  snapshots: { workflow: "refresh-market-snapshots.yml", publicationPaths: ["data/market"], commitMessage: "Refresh NAVER market snapshots" },
  diagnostic: { workflow: "schedule-diagnostic.yml", publicationPaths: [] },
};

export const SLOT_GRACE_BEFORE_MS = 5 * 60 * 1000;
export const SLOT_GRACE_AFTER_MS = 15 * 60 * 1000;

export function planForCron(cron) {
  if (cron === "30 11 * * 1-5") {
    return { slot: "20:30", key: "refresh-final", ...WORKFLOWS.refresh, inputs: { run_mode: "final_recovery" } };
  }
  if (cron === "0 23 * * 0-4" || cron === "7 0-10 * * 1-5") {
    return { slot: cron === "0 23 * * 0-4" ? "08:00" : "09:07-19:07", key: "refresh-intraday", ...WORKFLOWS.refresh, inputs: { run_mode: "intraday" } };
  }
  if (cron === "0 14 * * 1-5") return { slot: "23:00", key: "daily-report", ...WORKFLOWS.report, inputs: {} };
  if (cron === "17 0 * * 1-5") return { slot: "09:17", key: "schedule-diagnostic", ...WORKFLOWS.diagnostic, inputs: {} };
  if (cron === "20 22 * * 0-4" || cron === "20 3 * * 1-5" || cron === "20 10 * * 1-5") {
    return { slot: "research", key: "research", ...WORKFLOWS.research, inputs: {} };
  }
  if (cron === "10,30,50 23 * * 0-4" || cron === "10,30,50 0-10 * * 1-5") {
    return { slot: "snapshots", key: "snapshots", ...WORKFLOWS.snapshots, inputs: {} };
  }
  return null;
}

function kstParts(scheduledTime) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(scheduledTime));
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
}

export function slotKeyFor(plan, cron, scheduledTime) {
  const kst = kstParts(scheduledTime);
  return `${plan.key}:${kst.year}-${kst.month}-${kst.day}T${kst.hour}:${kst.minute}:${cron}`;
}

function headers(env) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
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
  if (!response.ok) throw new Error(`GITHUB_HTTP_${response.status}`);
  return body;
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

export async function verifyRunCompletion(api, repository, plan, run, options = {}) {
  const current = await api(`/repos/${repository}/actions/runs/${run.id}`);
  if (current.status !== "completed") {
    return { runId: current.id, runHead: current.head_sha, status: "RUN_STARTED" };
  }
  const result = { runId: current.id, runHead: current.head_sha,
    status: current.conclusion === "success" ? "RUN_VERIFIED" : "RUN_FAILED" };
  if (result.status !== "RUN_VERIFIED") return result;
  const publicationSha = current.production_commit_sha || current.outputs?.production_commit_sha;
  if (!publicationSha) {
    result.publicationStatus = "PUBLISH_UNVERIFIED";
    result.deliveryStatus = "UNVERIFIED";
    return result;
  }
  result.publicationSha = await commitChangedPaths(api, repository, [{ sha: publicationSha }], plan.publicationPaths, current, plan.commitMessage);
  result.publicationStatus = result.publicationSha ? "PUBLISHED" : "PUBLISH_UNVERIFIED";
  if (plan.key === "refresh-final" && result.publicationSha) {
    const expectedDate = options.expectedDate;
    Object.assign(result, await verifyFinalPublication(api, repository, expectedDate, result.publicationSha));
  }
  result.deliveryStatus = "UNVERIFIED";
  return result;
}

async function readState(env, slotKey) {
  const id = env.SLOT_GATE.idFromName(slotKey);
  const stub = env.SLOT_GATE.get(id);
  const response = await stub.fetch("https://slot-gate/state?slotKey=" + encodeURIComponent(slotKey));
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`SLOT_STATE_HTTP_${response.status}`);
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
  const state = result.status === "FINAL_PUBLICATION_VERIFIED" || result.status === "PUBLISHED" ? "PUBLISHED" : result.status;
  await setState(env, slotKey, state, { ...details, ...result });
  return { slotKey, workflow: plan.workflow, ...result };
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
  const existing = await findExistingRun(api, repository, plan, scheduledTime);
  if (existing) return { slotKey, workflow: plan.workflow, status: "EXISTING_RUN", runId: existing.id, runHead: existing.head_sha, runEvent: existing.event };

  const dispatchedAt = Date.now();
  await api(`/repos/${repository}/actions/workflows/${plan.workflow}/dispatches`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: plan.inputs }),
  });
  const creation = await waitForDispatchedRun(api, repository, plan, dispatchedAt, sleep);
  if (!creation.run) return { slotKey, workflow: plan.workflow, status: "RUN_UNVERIFIED" };
  const kst = kstParts(scheduledTime);
  const expectedDate = `${kst.year}${kst.month}${kst.day}`;
  const result = { slotKey, workflow: plan.workflow, runId: creation.run.id, runHead: creation.run.head_sha,
    runCreatedAt: creation.run.created_at, runUpdatedAt: creation.run.updated_at,
    expectedDate, status: creation.status };
  if (creation.status !== "COMPLETED") return result;
  return { ...result, ...(await verifyRunCompletion(api, repository, plan, creation.run, { expectedDate })) };
}

async function setState(env, slotKey, status, details = {}) {
  const id = env.SLOT_GATE.idFromName(slotKey);
  const stub = env.SLOT_GATE.get(id);
  await stub.fetch("https://slot-gate/state", { method: "POST", body: JSON.stringify({ slotKey, status, details }) });
}

async function claim(env, slotKey) {
  const id = env.SLOT_GATE.idFromName(slotKey);
  const stub = env.SLOT_GATE.get(id);
  const response = await stub.fetch("https://slot-gate/claim", { method: "POST", body: JSON.stringify({ slotKey }) });
  return response.status === 201;
}

export default {
  async scheduled(event, env, ctx) {
    const plan = planForCron(event.cron);
    if (!plan) return;
    const slotKey = slotKeyFor(plan, event.cron, event.scheduledTime);
    ctx.waitUntil((async () => {
      if (env.SCHEDULE_OBSERVE_ONLY === "true") {
        const result = await observeSlot((path, init) => github(env, path, init), env.GITHUB_REPOSITORY, plan, event.scheduledTime);
        console.log(JSON.stringify({ slotKey, ...result }));
        return;
      }
      if (env.SCHEDULE_RECHECK_ONLY === "true") {
        const result = await recheckRun(env, slotKey, plan);
        console.log(JSON.stringify({ slotKey, ...result }));
        return;
      }
      if (!(await claim(env, slotKey))) return;
      await setState(env, slotKey, "CLAIMED");
      try {
        await setState(env, slotKey, "DISPATCHED");
        const runResult = await dispatchAndVerify(env, plan, slotKey, event.scheduledTime);
        runResult.deliveryStatus = "UNVERIFIED";
        const result = { ...runResult, scheduledTime: event.scheduledTime };
        const state = result.status === "FINAL_PUBLICATION_VERIFIED" || result.status === "PUBLISHED" ? "PUBLISHED" : result.status;
        await setState(env, slotKey, state, result);
        console.log(JSON.stringify(result));
      } catch (error) {
        await setState(env, slotKey, "FAILED", { error: error.message });
        console.log(JSON.stringify({ slotKey, workflow: plan.workflow, status: "DISPATCH_FAILED" }));
      }
    })());
  },
};

export class SlotGate {
  constructor(state) { this.state = state; }

  async fetch(request) {
    if (request.method === "GET") {
      const slotKey = new URL(request.url).searchParams.get("slotKey");
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
    await this.state.storage.put(body.slotKey, { status: "CLAIMED", claimedAt: new Date().toISOString() }, { expirationTtl: 172800 });
    return new Response("claimed", { status: 201 });
  }
}

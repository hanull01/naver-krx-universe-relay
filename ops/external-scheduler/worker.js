const WORKFLOWS = {
  refresh: { workflow: "refresh.yml", publicationPaths: ["data/monitoring/latest-quality.json"] },
  report: { workflow: "daily-report.yml", publicationPaths: ["data/reports/latest.json"] },
  research: { workflow: "refresh-research.yml", publicationPaths: ["data/research/latest.json"] },
  snapshots: { workflow: "refresh-market-snapshots.yml", publicationPaths: ["data/market"] },
  diagnostic: { workflow: "schedule-diagnostic.yml", publicationPaths: [] },
};

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

async function claim(env, slotKey) {
  const id = env.SLOT_GATE.idFromName(slotKey);
  const stub = env.SLOT_GATE.get(id);
  const response = await stub.fetch("https://slot-gate/claim", {
    method: "POST",
    body: JSON.stringify({ slotKey }),
  });
  return response.status === 201;
}

async function dispatchAndVerify(env, plan, slotKey, scheduledTime) {
  const repository = env.GITHUB_REPOSITORY;
  const before = new Date(scheduledTime - 60_000).toISOString();
  await github(env, `/repos/${repository}/actions/workflows/${plan.workflow}/dispatches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: plan.inputs }),
  });

  let run = null;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5000));
    const result = await github(env, `/repos/${repository}/actions/workflows/${plan.workflow}/runs?event=workflow_dispatch&branch=main&per_page=10`);
    run = (result.workflow_runs || []).find((item) => item.created_at >= before);
    if (run) break;
  }
  if (!run) return { slotKey, workflow: plan.workflow, status: "RUN_UNVERIFIED" };

  const runResult = { slotKey, workflow: plan.workflow, runId: run.id, runHead: run.head_sha, status: "RUN_STARTED" };
  if (run.status !== "completed") return runResult;
  runResult.status = run.conclusion === "success" ? "RUN_SUCCESS" : "RUN_FAILED";
  if (runResult.status !== "RUN_SUCCESS" || !plan.publicationPaths.length) return runResult;

  const commits = await github(env, `/repos/${repository}/commits?sha=main&since=${encodeURIComponent(run.created_at)}&per_page=20`);
  for (const commit of commits || []) {
    const detail = await github(env, `/repos/${repository}/commits/${commit.sha}`);
    const filenames = (detail.files || []).map((file) => file.filename || "");
    if (plan.publicationPaths.some((path) => filenames.some((name) => name === path || name.startsWith(`${path.replace(/\/$/, "")}/`)))) {
      runResult.publicationSha = commit.sha;
      break;
    }
  }
  runResult.publicationStatus = runResult.publicationSha ? "VERIFIED_BY_COMMIT" : "UNVERIFIED";
  runResult.deliveryStatus = "UNVERIFIED";
  return runResult;
}

export default {
  async scheduled(event, env, ctx) {
    const plan = planForCron(event.cron);
    if (!plan) return;
    const date = new Date(event.scheduledTime).toISOString().slice(0, 10);
    const slotKey = `${plan.key}:${date}:${event.cron}`;
    ctx.waitUntil((async () => {
      if (!(await claim(env, slotKey))) return;
      try {
        const result = await dispatchAndVerify(env, plan, slotKey, event.scheduledTime);
        console.log(JSON.stringify(result));
      } catch (error) {
        console.log(JSON.stringify({ slotKey, workflow: plan.workflow, status: "DISPATCH_FAILED", error: error.message }));
      }
    })());
  },
};

export class SlotGate {
  constructor(state) { this.state = state; }

  async fetch(request) {
    if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
    const { slotKey } = await request.json();
    if (!slotKey || await this.state.storage.get(slotKey)) return new Response("duplicate", { status: 409 });
    await this.state.storage.put(slotKey, { claimedAt: new Date().toISOString() }, { expirationTtl: 172800 });
    return new Response("claimed", { status: 201 });
  }
}

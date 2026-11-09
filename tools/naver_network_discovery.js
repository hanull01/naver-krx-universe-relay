#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const OUTPUT = process.argv[2] || path.join(process.cwd(), 'data/diagnostics/naver-network-discovery.json');
const PAGES = [
  { name: 'pc_industry', url: 'https://finance.naver.com/sise/sise_group.naver?type=upjong' },
  { name: 'pc_theme', url: 'https://finance.naver.com/sise/theme.naver' },
  { name: 'mobile_industry', url: 'https://m.stock.naver.com/domestic/home/theme/daily?domesticStockExchange=KRX', interactionText: '업종' },
  { name: 'mobile_theme', url: 'https://m.stock.naver.com/domestic/theme' },
];

const SECRET_KEYS = /cookie|token|secret|password|passwd|authorization|credential|private|api[_-]?key|sentry/i;

function safeUrl(raw) {
  try {
    const url = new URL(raw);
    for (const key of [...url.searchParams.keys()]) {
      if (SECRET_KEYS.test(key)) url.searchParams.set(key, '[REDACTED]');
    }
    return url.toString();
  } catch (_) {
    return '[INVALID_URL]';
  }
}

function safePostData(value) {
  if (!value) return null;
  return String(value).split('&').map((part) => {
    const [key, ...rest] = part.split('=');
    return `${key}=${SECRET_KEYS.test(key) ? '[REDACTED]' : rest.join('=')}`;
  }).join('&');
}

function schemaSummary(value) {
  if (value === null) return { root: 'null' };
  if (Array.isArray(value)) {
    return {
      root: 'array',
      rowCount: value.length,
      firstRowKeys: value[0] && typeof value[0] === 'object' ? Object.keys(value[0]).slice(0, 80) : [],
    };
  }
  if (typeof value !== 'object') return { root: typeof value };
  const arrays = {};
  for (const [key, item] of Object.entries(value)) {
    if (Array.isArray(item)) arrays[key] = item.length;
  }
  const dateFields = {};
  for (const key of Object.keys(value)) {
    if (/date|time|at|from|to|cursor|page|next|total|count/i.test(key)) dateFields[key] = value[key];
  }
  return { root: 'object', keys: Object.keys(value).slice(0, 100), arrays, dateFields };
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function getPageTarget() {
  const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
  const page = targets.find((item) => item.type === 'page' && item.url === 'about:blank') || targets.find((item) => item.type === 'page');
  if (!page) throw new Error('CDP_PAGE_NOT_FOUND');
  return page.webSocketDebuggerUrl;
}

async function connect(wsUrl) {
  const socket = new WebSocket(wsUrl);
  const pending = new Map();
  const eventHandlers = [];
  let nextId = 1;
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const resolve = pending.get(message.id);
      pending.delete(message.id);
      resolve(message);
      return;
    }
    for (const handler of eventHandlers) handler(message);
  });
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, (message) => message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result || {}));
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { send, eventHandlers, close: () => socket.close() };
}

async function collectPage(cdp, page) {
  const requests = new Map();
  const responses = new Map();
  const bodyTasks = [];
  let loadSeen = false;
  const handler = (message) => {
    const { method, params } = message;
    if (method === 'Page.loadEventFired') loadSeen = true;
    if (method === 'Network.requestWillBeSent') {
      requests.set(params.requestId, {
        requestId: params.requestId,
        url: safeUrl(params.request.url),
        method: params.request.method,
        resourceType: params.type,
        postData: safePostData(params.request.postData),
        initiator: params.initiator?.type || null,
      });
    }
    if (method === 'Network.responseReceived') {
      const request = requests.get(params.requestId) || { requestId: params.requestId, url: safeUrl(params.response.url) };
      const response = {
        ...request,
        status: params.response.status,
        mimeType: params.response.mimeType,
        contentType: params.response.headers?.['content-type'] || params.response.headers?.['Content-Type'] || null,
        protocol: params.response.protocol || null,
        fromDiskCache: params.response.fromDiskCache || false,
      };
      responses.set(params.requestId, response);
    }
    if (method === 'Network.loadingFinished' && responses.has(params.requestId)) {
      const response = responses.get(params.requestId);
      if (response.resourceType === 'XHR' || response.resourceType === 'Fetch' || /json/i.test(response.mimeType || response.contentType || '')) {
        bodyTasks.push(cdp.send('Network.getResponseBody', { requestId: params.requestId }).then((body) => {
          const text = body.body || '';
          response.bodyBytes = text.length;
          try { response.schema = schemaSummary(JSON.parse(text)); }
          catch (_) { response.schema = { root: 'non-json-or-invalid', prefix: text.slice(0, 120).replace(/\s+/g, ' ') }; }
        }).catch((error) => { response.bodyError = String(error.message || error); }));
      }
    }
  };
  cdp.eventHandlers.push(handler);
  await cdp.send('Network.enable');
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Page.navigate', { url: page.url });
  await sleep(7000);
  await cdp.send('Runtime.evaluate', { expression: 'window.scrollTo(0, document.body.scrollHeight)' });
  await sleep(1200);
  const interaction = await cdp.send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => {
      const labels = ['다음', '더보기', '다음 페이지', 'Next'];
      if (${JSON.stringify(page.interactionText || '')}) labels.unshift(${JSON.stringify(page.interactionText || '')});
      const nodes = [...document.querySelectorAll('a,button')];
      const industryInput = document.querySelector('input[code="industry"]');
      const node = industryInput?.closest('li')?.querySelector('label') || nodes.find((item) => labels.some((label) => (item.textContent || '').trim().includes(label)));
      if (node) { node.click(); return {clicked: true, text: (node.textContent || '').trim().slice(0, 80)}; }
      return {clicked: false};
    })()`,
  });
  await sleep(2500);
  await Promise.all(bodyTasks);
  const pageInfo = await cdp.send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => ({
      title: document.title,
      url: location.href,
      bodyPrefix: (document.body?.innerText || '').slice(0, 500),
      scripts: [...document.scripts].map((item) => item.src).filter(Boolean),
      links: [...document.querySelectorAll('a')].slice(0, 100).map((item) => ({text: (item.textContent || '').trim().slice(0, 80), href: item.href})),
    }))()`,
  });
  cdp.eventHandlers.splice(cdp.eventHandlers.indexOf(handler), 1);
  const entries = [...responses.values()].filter((entry) => entry.status !== undefined);
  return {
    name: page.name,
    url: page.url,
    loadSeen,
    interaction: interaction.result?.value || null,
    pageInfo: pageInfo.result?.value || null,
    requests: entries,
  };
}

async function main() {
  fs.mkdirSync(path.dirname(OUTPUT), { recursive: true });
  const wsUrl = await getPageTarget();
  const cdp = await connect(wsUrl);
  const results = [];
  try {
    for (const page of PAGES) results.push(await collectPage(cdp, page));
  } finally {
    cdp.close();
  }
  const artifact = {
    schemaVersion: 'naver-network-discovery-v1',
    generatedAt: new Date().toISOString(),
    browser: 'Chrome CDP headless read-only',
    pages: results,
    notes: [
      'No cookies, authorization headers, credentials, or response bodies are persisted.',
      'A candidate endpoint is not production-approved by this diagnostic alone.',
    ],
  };
  fs.writeFileSync(OUTPUT, JSON.stringify(artifact, null, 2) + '\n', { mode: 0o600 });
  const summary = results.map((item) => ({
    name: item.name,
    url: item.url,
    requestCount: item.requests.length,
    jsonLike: item.requests.filter((entry) => /json/i.test(entry.mimeType || entry.contentType || '')).length,
    apiUrls: item.requests.filter((entry) => /\/api\/|json|ajax|graphql/i.test(entry.url)).map((entry) => ({url: entry.url, method: entry.method, status: entry.status, schema: entry.schema})).slice(0, 80),
    interaction: item.interaction,
  }));
  console.log(JSON.stringify({output: OUTPUT, summary}, null, 2));
}

main().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });

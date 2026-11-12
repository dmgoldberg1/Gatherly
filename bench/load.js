import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import exec from 'k6/execution';
import { open as openFile, SeekMode } from 'k6/experimental/fs';

const config = JSON.parse(open('./config.json'));
const data = JSON.parse(open('./data/accounts.json'));
// File contents are shared across VUs. Only photo VUs allocate a binary buffer;
// avoid base64/JSON strings, whose copies can exhaust a small load-generator VM.
const photos = await Promise.all(['small', 'medium', 'large'].map((name) => openFile(`./data/${name}.jpg`)));
let photoBuffer;
const base = __ENV.BASE_URL;
const successes = new Counter('bench_success');
const errors = new Rate('bench_errors');
const latency = new Trend('bench_duration', true);
const seconds = (value) => Number(value.slice(0, -1));
const scenarios = {};
const thresholds = {};
for (const [name, key] of [['feed', 'feed_rps'], ['recommendations', 'recommendations_rps'], ['photo_upload', 'photo_upload_rps']]) {
  if (!config[key]) continue;
  const start = name === 'photo_upload' ? seconds(config.photo_start_after) : 0;
  scenarios[name] = {
    executor: 'constant-arrival-rate', rate: config[key], timeUnit: '1s',
    startTime: `${start}s`, duration: `${seconds(config.duration) - start}s`,
    preAllocatedVUs: Math.max(4, Math.ceil(config[key] * 2)),
    maxVUs: Math.max(16, config[key] * 10), exec: name, gracefulStop: '60s',
  };
  thresholds[`bench_success{scenario:${name}}`] = ['count>=0'];
  thresholds[`bench_duration{scenario:${name}}`] = ['p(99)<60000'];
  thresholds[`bench_errors{scenario:${name}}`] = ['rate<0.05'];
  thresholds[`dropped_iterations{scenario:${name}}`] = ['count<1000000000'];
}
export const options = {scenarios, thresholds, summaryTrendStats: ['avg', 'p(95)', 'p(99)', 'max']};

function account() {
  return data.accounts[(exec.scenario.iterationInTest + config.seed) % data.accounts.length];
}

function record(response, expected) {
  const ok = response.status === expected;
  if (ok) successes.add(1);
  else successes.add(0);
  errors.add(!ok);
  latency.add(response.timings.duration);
}

export function feed() {
  record(http.get(`${base}/api/v1/events?limit=50`, {tags: {name: 'public_feed'}}), 200);
}

export function recommendations() {
  record(http.get(`${base}/api/v1/recommendations?limit=10`, {headers: {Authorization: `Bearer ${account().token}`}, tags: {name: 'recommendations'}}), 200);
}

export async function photo_upload() {
  const user = account();
  if (Date.now() / 1000 >= user.upload_expires_at - 30) {
    const renewed = http.post(`${base}/api/v1/albums/${user.album_id}/upload-token`, '{}', {
      headers: {'Content-Type': 'application/json', Authorization: `Bearer ${user.token}`}, tags: {name: 'control_token_refresh'},
    });
    if (renewed.status !== 200) { record(renewed, 200); return; }
    user.upload_token = renewed.json('upload_token');
    user.upload_expires_at = Date.now() / 1000 + renewed.json('expires_in');
  }
  const file = photos[(exec.scenario.iterationInTest + config.seed) % photos.length];
  const info = await file.stat();
  if (!photoBuffer || photoBuffer.length < info.size) photoBuffer = new Uint8Array(info.size);
  await file.seek(0, SeekMode.Start);
  const photo = photoBuffer.subarray(0, info.size);
  if (await file.read(photo) !== info.size) throw new Error('Incomplete photo fixture');
  record(http.post(`${base}/api/v1/albums/${user.album_id}/photos`, photo.buffer.slice(0, info.size), {
    headers: {'Content-Type': 'image/jpeg', 'X-Upload-Token': user.upload_token}, tags: {name: 'photo_upload'}, timeout: '60s',
  }), 201);
}

export function handleSummary(data) {
  const report = {config, scenarios: {}};
  for (const [name, scenario] of Object.entries(scenarios)) {
    const values = (metric) => data.metrics[`${metric}{scenario:${name}}`]?.values || {};
    const active = seconds(scenario.duration);
    report.scenarios[name] = {
      target_rps: scenario.rate,
      successful_rps: (values('bench_success').count || 0) / active,
      successful_requests: values('bench_success').count || 0,
      p95_ms: values('bench_duration')['p(95)'] ?? null,
      p99_ms: values('bench_duration')['p(99)'] ?? null,
      error_rate: values('bench_errors').rate ?? null,
      dropped_iterations: values('dropped_iterations').count || 0,
    };
  }
  return {'/results/summary.json': JSON.stringify(data, null, 2), '/results/report.json': JSON.stringify(report, null, 2), stdout: JSON.stringify(report, null, 2) + '\n'};
}

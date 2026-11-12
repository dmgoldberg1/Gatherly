# Completed checks

[Русский](README.md) | **English**

- Before new features were added, the Python migration passed the existing
  `scripts/smoke.py` and `scripts/integration.py` checks: concurrent joins under
  capacity limits, waitlist promotion, transactional publication rollback after
  outbox failure, delivery and JWT recovery after restart, PostgreSQL failure and
  recovery, dump restoration, and Prometheus configuration with four scrape targets.
- Additional HTTP contracts: pagination and errors, follows, three visibility
  modes, partial updates, and selected notification recipients.
- Twenty-one Python tests: password hash/JWT compatibility, photo processing and
  token limits, recency weighting, SQL-free authorization and warm ranking,
  exclusion of existing participations, popular fallback, input validation,
  repeated CDS application, and unchanged endpoints when switching policy.
- Photos through the running proxy: permissions, album-bound tokens, invalid
  images, image variants, receipts, publication, and safe retries.
- Playwright/Chromium: sign-in, recommendations, album creation, upload,
  publication, and image display. Viewport widths of 1440 and 390 px were checked.
- The actual pinned Sentence Transformers model on CPU: profiles with and without
  history were prepared; personalized and popular responses were verified.
- Short k6 runs: one replica with round_robin and three replicas with round_robin
  and least_request. Each ran for 15 seconds at 5/3/1 RPS, with photos starting at
  second 5. All scenarios had zero errors and dropped iterations. Raw p95/p99
  and successful RPS values are in the adjacent JSON files.
- After warming caches on all three replicas, PostgreSQL was stopped:
  authenticated recommendations and photo uploads continued to succeed.
  The database was then restarted and background processing recovered.
- Image dependencies (`pip check`), Compose, Terraform schemas, Ansible syntax,
  and GitHub Actions (`actionlint`) were checked.

## Cloud checks

`object-storage.json` records an actual private Yandex Object Storage check:
Pillow processing, PUT/GET/HEAD, two image variants, and a readiness marker.
Objects created by this test were removed.

The early cloud checks below used a small compact stand: Envoy, PostgreSQL,
the CPU worker, monitoring, and replicas shared a preemptible VM in
`ru-central1-a` with 2 vCPU, 8 GiB RAM, and a 10 GiB SSD. k6 ran on a separate
preemptible VM in `ru-central1-d` with 2 vCPU, 2 GiB RAM, and a 15 GiB HDD.
The configuration is recorded in `cloud-hardware-small.json`.

Cloud smoke, compatibility, and photo album checks through private Object Storage
passed: `cloud-http.json`. All Grafana panels receive data, including CPU and
queue metrics for each replica: `cloud-panels.json`.

Short cloud k6 runs with one and three replicas using `round_robin`, and three
replicas using `least_request`, passed without errors or dropped iterations:
`cloud-one-round-robin.json`, `cloud-three-round-robin.json`, and
`cloud-three-least-request.json`. Each ran for 30 seconds at 5/3/1 RPS, with
photos starting at second 10. The actual healthy replica count and active Envoy
policy were checked before every run; verification files are stored beside the reports.

The full 180-second run with `least_request`, three replicas, and 50/30/5 RPS
completed but **failed the k6 error threshold**: `cloud-full-least-request.json`
and the raw `cloud-full-least-request-summary.json`. The feed achieved 49.99
successful RPS with 2 errors; recommendations achieved 30.01 RPS without errors;
photos achieved 3.74 RPS, with 24.16% errors and 9 dropped iterations. p99 was
353 / 331 / 8584 ms respectively. This measures the selected small stand and
does not confirm support for the target rate of 5 photos/s.

`cloud-full-diagnostics.json` shows full queues on all three replicas
(2 active processing tasks and 4 waiting), HTTP 429, and peak usage around two
CPU cores. Replicas did not restart and there were no OOM events.
`cloud-runtime-sample.txt` is a single resource snapshot taken during this run;
it does not measure peak memory usage.

An additional 180-second run at **50/30/1 RPS** with three replicas passed without
errors or dropped iterations: `cloud-50-30-1-least-request.json`.
Reapplying the same configuration was checked separately locally;
cloud reapplication was not tested.

After the resource upgrade, both VMs—the server in `ru-central1-a` and the
separate k6 VM in `ru-central1-d`—have **8 vCPU (100%), 16 GiB RAM, and a 20 GiB SSD**
and remain preemptible. Actual settings are in `cloud-hardware.json`.
The server was updated without recreation; business table content hashes,
database rows, previous containers, and images were preserved:
`cloud-hardware-upgrade.json`.

**One one-minute run with 10 replicas**, `least_request`, and 50/30/5 RPS was
completed: [report](cloud-ten-replicas-60s.en.md), `cloud-ten-least-request-60s.json`,
and the raw `cloud-ten-least-request-60s-summary.json`. Photos ran from second 15
for 45 seconds. All target rates were met, with zero errors and dropped iterations.
p99 was 14.13 ms for the feed, 12.51 ms for recommendations, and 889.31 ms for photos.
The active policy and 10 healthy replicas were verified before measurement:
`cloud-ten-least-request-60s-verification.json`. All 15 Prometheus targets were
available and Grafana panels received data: `cloud-ten-monitoring.json`.
The run has finished and k6 is no longer running.

Local Terraform state and private settings are kept outside Git; reapplication
continues managing the same stand. The recorded state is in `cloud-status.json`.

The maximum replica count actually tested is **3 locally and 10 in the cloud**.
The supported 1–10 range is validated before cloud changes. Role generation was
checked for 1, 3, and 10 replicas, corresponding to 5, 7, and 14 VMs. The earlier
cloud run with ten replicas used the compact layout; it did not use ten dedicated
application VMs. The load benchmark workflow was prepared and passed actionlint,
but had not been run through GitHub Actions.

The distributed layout is prepared but has not been applied: Yandex is processing
RAM and public IP quota request `atdiu9g4sa43fmgm8gbl`. The existing compact stand
is unchanged. `distributed-config-plans.json` records Terraform plan checks on a
private state copy for `replicas=1,3,10`: 5, 7, and 14 VMs respectively, preserving
the database VM without deletions. The default remains 3 replicas; the effective
configuration determines the cloud machine count.

Actions setup on November 12, 2026 is recorded in `actions-bootstrap.json`.
Eight variables and six secrets are configured in GitHub; the persistent database
password and auth secret were retained. A dedicated CI account with the approved
roles and a dedicated SSH key were created; the key was verified on both existing
VMs. State `serial=42` was copied to a private versioned bucket and local Terraform
switched to the S3 backend. A refresh-only plan using the CI account passed
without resource drift, acquiring and releasing the S3 lock. All 30 Python unit
tests passed in the existing Python 3.12 application image with networking disabled.

The first manually triggered regular CI run on `main` succeeded:
[run 43241143264](https://github.com/dmgoldberg1/Gatherly/actions/runs/43241143264).
`build-test` checked both image builds, compatibility, HTTP, PostgreSQL failures,
recovery, backup, and monitoring. `infrastructure` checked Terraform, Ansible,
workflows, and JavaScript. `publish` was skipped by the manual-run condition.
This CI tested published `main` (`075f29e`); the new setup changes and 30 unit
tests were checked separately in the local working tree.

The first profile is prepared: one dedicated replica, `round_robin`, 60 seconds,
50/30/5 RPS, photos starting at second 15, seed 42. `load-bench.yml` has not been
started: RAM in `ru-central1-a` is still limited to 16 GiB, used by the database VM.
This profile needs 64 GiB in the application zone and another 16 GiB in the k6 zone.
The RAM item in the existing quota request remains `PROCESSING`. No new VMs were
created; the maximum dedicated cloud replica count tested in this stage is 0.

Envoy profiles were tested separately using actual Envoy `v1.37.1` and three
temporary HTTP backends: eight reference profiles plus one configuration with
modified route timeout and health settings. Results: [envoy-profiles.json](envoy-profiles.json).
Checks covered CDS/RDS without restarts, weights 1:2:1, hash-key affinity, the native
extension interface, preflight rejection of unknown fields, and detection of a
rejected live update. All 38 unit tests passed in the Python image with PyYAML.
Workflows passed actionlint, the playbook passed Ansible syntax checks, and the
load script passed `k6 inspect`. These checks were local; no new cloud run was performed.

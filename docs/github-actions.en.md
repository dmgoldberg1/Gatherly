# Running the load test stand through GitHub Actions

[Русский](github-actions.md) | **English**

Workflow: `.github/workflows/load-bench.yml`, named **Gatherly cloud load benchmark**.
Each application replica runs on its own VM. Envoy, PostgreSQL,
Prometheus/Grafana, and k6 also use separate VMs. `replicas` selects
1–10 application VMs; the total is `replicas + 4` machines.

## One-time setup

The workflow and its Python files, Dockerfiles, migrations, Terraform, and Ansible
must be present on the repository's default branch. The **Run workflow** button
appears when `workflow_dispatch` is defined on that branch.

Under **Settings → Secrets and variables → Actions → Variables**, set:

| Variable | Value |
| --- | --- |
| `YC_CLOUD_ID` | Cloud ID |
| `YC_FOLDER_ID` | Stand folder ID |
| `BENCH_BUCKET` | Name of the private photo and recommendation snapshot bucket |
| `BENCH_STATE_BUCKET` | Name of a separate private Terraform state bucket |
| `BENCH_STAND_NAME` | Optional; defaults to `gatherly-bench` |
| `YC_ZONE` | Optional; defaults to `ru-central1-a` |
| `BENCH_LOAD_ZONE` | Optional; defaults to `ru-central1-d` |
| `BENCH_PREEMPTIBLE` | Optional; defaults to `true` |

VMs have 8 vCPU (100%), 16 GiB RAM, and a 20 GiB SSD. Quotas must cover the
selected machine count: ten replicas need 14 VMs, 112 vCPU, 224 GiB RAM, and
280 GiB SSD. One VM consumes resources in the k6 zone; the rest use `YC_ZONE`.

Create the state bucket in advance, enable versioning, and give the service
account access to read and write state and delete lock objects.
The stand's Terraform module does not create or delete this bucket.

Under **Secrets**, set:

| Secret | Purpose |
| --- | --- |
| `YC_SA_KEY_JSON` | Complete service account authorized key JSON containing `id`, `service_account_id`, and `private_key` |
| `BENCH_STATE_ACCESS_KEY` | Static access key ID for the state bucket |
| `BENCH_STATE_SECRET_KEY` | Secret access key for the state bucket |
| `BENCH_SSH_PRIVATE_KEY` | Private SSH key for the stand's VMs |
| `BENCH_DB_PASSWORD` | Persistent database password, 64 hexadecimal characters |
| `BENCH_AUTH_SECRET` | Persistent JWT and signed photo token secret, at least 32 bytes |

The service account needs permission to manage the stand's VMs, network,
Object Storage bucket, object access service account, and its static keys.
Keep the database password, JWT secret, and SSH key unchanged between deploy
and update_config. Use the existing values for an already running stand.

## Migrating existing local state

If the stand was created locally, first migrate its state to the shared bucket.
This lets independent GitHub runners manage the same VMs, network, and database.

In a local Python 3.12 environment with Terraform installed:

```bash
python3.12 -m venv .local/actions-venv
. .local/actions-venv/bin/activate
python -m pip install -r bench/requirements-actions.txt

export BENCH_STATE_BUCKET='<state-bucket>'
export BENCH_STAND_NAME='gatherly-bench'
export AWS_ACCESS_KEY_ID='<state-access-key>'
export AWS_SECRET_ACCESS_KEY='<state-secret-key>'
python bench/cloud.py migrate_state
```

The command copies state from `infra/terraform/bench/terraform.tfstate` to
`<BENCH_STAND_NAME>/terraform.tfstate`, retaining a local copy. The initial write
is conditional and cannot overwrite an existing remote object. Repeating the
operation with identical state is safe; different state, including newer state,
is rejected. Regular deploy does not silently switch local state to a remote backend.
After the switch, Terraform may clear `terraform.tfstate` and save the original
document in `terraform.tfstate.backup`. A repeated migration uses this copy only
when the configured backend matches the requested bucket/key. Validation check
ordering does not affect comparison; changes to data, lineage, serial, or check
status are still rejected.

A new stand without local state does not require this step.

### Uploading settings through GitHub CLI

Authenticate locally; do not paste the token into chat or commit it to Git:

```bash
GH_CONFIG_DIR="$PWD/.local/gh" .local/bin/gh auth login \
  --hostname github.com --git-protocol https --web --scopes repo,workflow
export GH_CONFIG_DIR="$PWD/.local/gh"
```

You can use a system installation of `gh`. The local `.local/bin/gh` copy is not
included in the repository. A fine-grained PAT needs write access to Actions,
Secrets, and Variables on Gatherly; workflow changes also require Contents/Workflows.
See the [GitHub REST API](https://docs.github.com/en/rest/actions/secrets) for exact Secrets permissions.

`python -m bench.github_setup` accepts a private JSON file with three fields:
`repository` (for example, `dmgoldberg1/Gatherly`), `variables`, and `secrets`.
The last two are objects mapping the names in the tables above to string values.
All six secrets are required. For an existing stand, use the database password
and auth secret from its current environment. Create a dedicated Actions SSH key,
add its public key to the existing VMs' `authorized_keys`, and verify access
before the run. Do not upload your personal SSH key to Actions.

Store the JSON under `.local/bench/actions/`, with directory permissions `700`
and file permissions `600`. After preparing the cloud account and keys:

```bash
chmod 600 .local/bench/actions/github-settings.json
python -m bench.github_setup validate --settings .local/bench/actions/github-settings.json
python -m bench.github_setup configure --settings .local/bench/actions/github-settings.json
python -m bench.github_setup check --settings .local/bench/actions/github-settings.json
```

`configure` checks API access and the absence of an active benchmark workflow
before making any changes. Secrets are passed to `gh secret set` through stdin;
values are not printed or included in process arguments. `check` compares variable
values and checks all six secret names; GitHub does not return secret values.

### CI permissions for an existing stand

Once the network, photo bucket, and `objects` account exist, CI does not need
folder-wide IAM administration. The prepared permissions are:

| Scope | Roles |
| --- | --- |
| Stand folder | `compute.editor`, `vpc.user`, `vpc.publicAdmin`, `vpc.securityGroups.admin` |
| Existing `objects` account only | `viewer`, `iam.serviceAccounts.accessKeyAdmin` |
| Photo bucket only | `storage.admin` |
| State bucket only | `storage.editor` |

An operator creates the CI account, authorized key, and static key. Folder-level
permissions let CI create/delete VMs and change network access within that folder;
use a folder dedicated to the stand. This account is intended to update existing
infrastructure, rather than create a new stand from scratch. Bucket-specific
roles are described in the [Yandex Cloud documentation](https://yandex.cloud/en/docs/storage/operations/buckets/iam-access).

You can seed state using the operator's current IAM token through
`bench.backend.seed(..., client=...)`, without creating a persistent key.
The function uses a conditional write and verifies the document by reading it
back; different remote state is not overwritten. This creates a prepared copy
while the local backend remains active. After issuing the keys, run the regular
`migrate_state` command above to switch to the shared bucket. Do not manage the
same stand concurrently through the old local backend.

## First one-minute run

Open **Actions → Gatherly cloud load benchmark → Run workflow**:

1. `mode`: `deploy_and_test`.
2. `config_file`: `bench/configs/one-minute.json`.
3. `replicas`: a number from 1 to 10; an empty input keeps the file's value of 3.
4. Leave the other inputs empty to use the selected JSON.

The one-minute profile uses 50 RPS for the feed, 30 RPS for recommendations, and
5 RPS for photos. Photo uploads start at second 15 and last 45 seconds. Without
selecting a profile, `bench/defaults.json` uses 180 seconds with photos starting at second 60.

Execution order: `cleanup_workspace → deploy → load_test`. The effective JSON
is validated before cloud changes, shown in the summary, and passed to all jobs
in a single artifact. Workflow inputs override their corresponding JSON values.
Invalid parameters stop the run before deploy.

To start a run with one dedicated replica through CLI after configuring
credentials, switching the backend, and checking quotas:

```bash
.local/bin/gh workflow run load-bench.yml --repo dmgoldberg1/Gatherly --ref main \
  -f mode=deploy_and_test -f config_file=bench/configs/one-minute.json -f replicas=1
.local/bin/gh run list --repo dmgoldberg1/Gatherly --workflow load-bench.yml --limit 5
.local/bin/gh run watch <RUN_ID> --repo dmgoldberg1/Gatherly --exit-status
```

For `replicas=1`, `YC_ZONE` needs 64 GiB RAM for the database, Envoy, monitoring,
and application. k6 needs another 16 GiB in its zone: 5 VMs, 40 vCPU, 80 GiB RAM,
100 GiB SSD, and 5 IP addresses in total. These totals include existing VMs.
Before building images and deploying, check available quota while accounting
for other cloud resources. A prepared state copy and an available workflow do
not mean that a distributed load test has been completed.

When migrating from the compact layout, deploy preserves the existing database
VM by moving its Terraform state address. Database VM deletion or replacement
is blocked. Once dedicated replicas and Envoy are ready, old application and
monitoring containers are removed from the database VM; the PostgreSQL volume is preserved.

## Updating a running stand

Select `mode=update_config`, the same `config_file`, and new `replicas` or `policy`
values. Enable `run_load_test` to run a test after the update.

The current images are reused. Terraform adds/removes only application VMs,
then updates Envoy CDS and Prometheus targets. PostgreSQL, the bucket, Envoy,
monitoring, and k6 are preserved. Only k6 receives changes to request rates and
duration. Operations on one stand are serialized; a new run does not cancel an active one.

## Results

The load_test summary shows target and successful RPS, p95, p99, error rate,
and dropped iterations for each scenario. The `load-test-results` artifact contains:

- `config.json`: one effective run configuration;
- `report.json` and `summary.json`: the summarized and raw k6 reports;
- `verification.json`: the active Envoy policy and healthy replica count;
- `*-logs.txt`: available diagnostics after an error.

Artifacts are retained for 14 days. They exclude Terraform state, SSH keys,
the service account JSON key, and `private.env`. A report with a different
configuration is not published. If k6 does not produce a report, the summary
states that the result is missing and points to diagnostics.

Grafana runs on the `monitoring` VM. Connect through an SSH tunnel:

```bash
ssh -N -L 3000:127.0.0.1:3000 ubuntu@<MONITORING_PUBLIC_IP>
```

Open `http://localhost:3000`, log in as `admin`, and use `BENCH_AUTH_SECRET` as the password.

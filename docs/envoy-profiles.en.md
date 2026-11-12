# Envoy profiles

[Русский](envoy-profiles.md) | **English**

Create a YAML or JSON file under `bench/envoy/`, save it in your selected branch,
and enter its path in the workflow's `envoy_config_file` input. To update a running
stand, use `mode=update_config`, the existing replica count, and `run_load_test=true`.
The profile takes precedence over `policy`.

## Format

```yaml
name: my-experiment
cluster:
  lb_policy: LEAST_REQUEST
  least_request_lb_config:
    choice_count: 4
  common_lb_config:
    healthy_panic_threshold:
      value: 0
route:
  timeout: 30s
endpoint_weights: [1, 2, 1]
```

- `name` is the experiment name shown in the report.
- `cluster` accepts native [Envoy Cluster](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/cluster/v3/cluster.proto) fields.
  Set exactly one of `lb_policy` or `load_balancing_policy`. You can configure
  algorithm parameters, health checks, circuit breakers, outlier detection, and protocols.
- `route` accepts native [RouteAction](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto) fields,
  such as `timeout`, `retry_policy`, and `hash_policy`.
- `endpoint_weights` assigns weights in `app-001`, `app-002`, … order. The number
  of weights must equal `replicas`. Omitting this field gives equal weights. The range is 1–128.
- `image` selects the Envoy image. The default is `envoyproxy/envoy:v1.37.1`.
  Pin a custom image to an immutable digest, as shown below.

The stand manages the cluster name, replica addresses and count, discovery type,
and routing destination. The `gatherly.bench` metadata namespace is reserved for
profile verification. Profiles cannot override these fields. Envoy itself validates
other cluster and route action fields; Python does not restrict them to a short allowlist.

## Hashing

The `ring-hash.yaml` and `maglev.yaml` profiles use:

```yaml
route:
  hash_policy:
    - header:
        header_name: X-Bench-Key
```

k6 sends `X-Bench-Key` in all three measured scenarios. Keys are deterministic
based on `seed` and the iteration number, cycling through 64 values. This allows
consistent hashing comparisons using the same key sequence.

## Native extensions and custom code

Use `load_balancing_policy` to configure Envoy's extension interface:

```yaml
name: my-extension-experiment
cluster:
  load_balancing_policy:
    policies:
      - typed_extension_config:
          name: envoy.load_balancing_policies.least_request
          typed_config:
            "@type": type.googleapis.com/envoy.extensions.load_balancing_policies.least_request.v3.LeastRequest
            choice_count: 4
```

See [least-request-extension.yaml](../bench/envoy/least-request-extension.yaml) for a working example.
For a new algorithm implemented in custom code, build and publish an Envoy image
with the extension, then select its public image by digest in the profile:

```yaml
name: my-custom-algorithm
image: ghcr.io/your-account/custom-envoy@sha256:YOUR_64_HEX_DIGEST
cluster:
  load_balancing_policy:
    policies:
      - typed_extension_config:
          name: your.load_balancing_policy
          typed_config:
            "@type": type.googleapis.com/your.package.Algorithm
            tuning_parameter: 4
```

Substitute the real digest, registered extension name, and protobuf type.
The image must contain the `envoy` binary and support the standard arguments
`--mode validate` and `-c`. Both the runner and the VMs must be able to pull it publicly.
The extension author builds the custom code and publishes the image.
Changing the image recreates the Envoy container; applications and the database are preserved.

## Validation and reproducibility

Before cloud changes, CI resolves the profile into one effective `config.json`
and validates the complete configuration using `envoy --mode validate` in the
selected image. An unknown field, missing extension, or invalid protobuf type
stops the run before deployment. The snapshot includes the profile contents and
image, so subsequent jobs and VMs do not read the source YAML again.

Cluster and route updates use atomic replacements of separate CDS and RDS files.
Before the measured phase, verification checks the profile fingerprint in both
resources, native parameters, and the exact number of healthy replicas. A
configuration that Envoy has not accepted is not treated as a successful update.
Migrating an older stand to RDS recreates the Envoy container once; subsequent
profile changes apply without a restart.

The `effective-config` artifact and k6 report contain the profile.
`verification.json` records its SHA-256 and CDS/RDS verification results.

To check all reference profiles and live updates against three temporary backends:

```bash
python -m pip install PyYAML==6.0.3
python -m bench.check_envoy
```

This check uses Docker, an isolated network, and temporary containers. It also
runs in regular CI. Results: `.local/bench/envoy-profiles-check.json`.

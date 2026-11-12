## Gatherly load benchmark

```json
{
  "version": 1,
  "replicas": 10,
  "policy": "least_request",
  "duration": "60s",
  "feed_rps": 50,
  "recommendations_rps": 30,
  "photo_upload_rps": 5,
  "photo_start_after": "15s",
  "seed": 42
}
```

| Scenario | Target RPS | Successful RPS | p95 ms | p99 ms | Error rate | Dropped iterations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feed | 50 | 50.02 | 10.901561 | 14.125642 | 0 | 0 |
| recommendations | 30 | 30.02 | 10.145054 | 12.509571 | 0 | 0 |
| photo_upload | 5 | 5 | 769.9781959999998 | 889.3148469999999 | 0 | 0 |

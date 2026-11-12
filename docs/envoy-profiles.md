# Профили Envoy

**Русский** | [English](envoy-profiles.en.md)

Создай YAML- или JSON-файл в `bench/envoy/`, сохрани его в выбранной ветке и укажи
путь в поле `envoy_config_file` при запуске workflow. Для применения к работающему
стенду используй `mode=update_config`, прежнее число реплик и `run_load_test=true`.
Профиль имеет приоритет над `policy`.

## Формат

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

- `name` — имя эксперимента в отчёте.
- `cluster` — нативные поля [Envoy Cluster](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/cluster/v3/cluster.proto).
  Задай ровно одно из `lb_policy` или `load_balancing_policy`. Можно настраивать
  параметры алгоритма, health checks, circuit breakers, outlier detection и протоколы.
- `route` — нативные поля [RouteAction](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto),
  например `timeout`, `retry_policy`, `hash_policy`.
- `endpoint_weights` — веса реплик в порядке `app-001`, `app-002`, …; число весов
  должно совпадать с `replicas`. Без этого поля веса одинаковые. Диапазон — 1–128.
- `image` — образ Envoy. Стандартный образ: `envoyproxy/envoy:v1.37.1`.
  Для своего образа укажи неизменяемый digest, как в примере ниже.

Стенд сам задаёт имя кластера, адреса и число реплик, тип обнаружения и назначение
маршрута. Пространство metadata `gatherly.bench` занято проверкой профиля.
Эти поля профилем не переопределяются. Остальные поля кластера и действия
маршрута проверяет сам Envoy, а не ограниченный список параметров в Python.

## Хеширование

Профили `ring-hash.yaml` и `maglev.yaml` используют:

```yaml
route:
  hash_policy:
    - header:
        header_name: X-Bench-Key
```

k6 отправляет `X-Bench-Key` во всех трёх измеряемых сценариях. Ключи детерминированы
по `seed` и номеру итерации, циклически используются 64 значения. Это позволяет
проверять consistent hashing на одинаковой последовательности ключей.

## Нативные расширения и собственный код

Поле `load_balancing_policy` позволяет использовать интерфейс расширений Envoy:

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

Готовый пример: [least-request-extension.yaml](../bench/envoy/least-request-extension.yaml).
Для нового алгоритма со своим кодом собери и опубликуй образ Envoy с расширением,
затем укажи в профиле его публичный образ по digest:

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

Подставь настоящий digest, имя зарегистрированного расширения и его protobuf-тип.
Образ должен содержать бинарный файл `envoy` и поддерживать стандартные аргументы
`--mode validate` и `-c`. Он должен быть доступен для публичного скачивания runner
и VM. Сборку собственного кода и публикацию образа выполняет автор расширения.
Переключение образа пересоздаёт контейнер Envoy; приложения и БД сохраняются.

## Проверка и воспроизводимость

До облачных изменений CI разрешает профиль в один итоговый `config.json` и
проверяет полный конфиг командой `envoy --mode validate` в выбранном образе.
Неизвестное поле, отсутствующее расширение или неверный protobuf-тип останавливают
запуск до deploy. Снимок включает содержимое профиля и образ, поэтому следующие
джобы и VM не читают исходный YAML повторно.

Кластер и маршрут обновляются атомарной заменой отдельных файлов CDS и RDS.
Перед измеряемой фазой проверяются fingerprint профиля в обоих ресурсах, нативные
параметры и точное число здоровых реплик. Непринятый Envoy конфиг не считается
успешным обновлением. При переходе с прежней версии стенда на RDS контейнер Envoy
однократно пересоздаётся; последующие изменения профиля применяются без перезапуска.

Профиль находится в артефакте `effective-config` и отчёте k6; `verification.json`
содержит его SHA-256 и результат проверки CDS/RDS.

Для проверки всех готовых профилей и переключений на трёх временных бэкендах:

```bash
python -m pip install PyYAML==6.0.3
python -m bench.check_envoy
```

Проверка использует Docker, изолированную сеть и временные контейнеры. Она также
запускается в обычном CI. Результат: `.local/bench/envoy-profiles-check.json`.

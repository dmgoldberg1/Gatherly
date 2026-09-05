# Мониторинг и проверка отказов

Prometheus хранит метрики три дня, максимум 512 МБ. Grafana автоматически получает
Prometheus datasource и дашборд **Gatherly — service health**. Для доступа:

```bash
ssh -N -L 3000:127.0.0.1:3000 ubuntu@PUBLIC_IP
```

Откройте http://localhost:3000, пользователь `admin`, пароль из локального
`infra/ansible/secrets.yml`. Если локальный 3000 занят, используйте
`-L 13000:127.0.0.1:3000` и http://localhost:13000.

## Что измеряется

| Сигнал | Метрика / панель |
|---|---|
| Traffic | `rate(http_requests_total[5m])`, группировка по шаблону route |
| Errors | доля HTTP 5xx; `notification_processing_errors_total` |
| Latency | p50/p95/p99 по `http_request_duration_seconds_bucket` |
| Saturation | `http_requests_in_flight`, CPU, память, свободное место, DB connections |
| Availability | `up{job="gatherly"}`, `gatherly_database_up`, `pg_up` |
| Outbox | pending/failed, возраст старейшей записи, deliveries/failures и длительность партии |

HTTP latency включает ожидание в рабочей очереди; `/health`, `/ready`, `/metrics`
исключены из пользовательского HTTP-трафика. Labels: method, известный шаблон route,
status. ID и произвольные URL не создают новые series. Накопительные counters
сбрасываются при рестарте; Prometheus `rate` это учитывает.

node_exporter читает proc/sys/root хоста: CPU, память и диск показывают VM.
postgres_exporter показывает доступность БД, connections и transactions, чтобы
отличить отказ PostgreSQL от перегрузки приложения. Для небольшого демо он
использует ту же локальную учётную запись БД; порт экспортера не опубликован.

Если трафика нет, квантили могут показывать No data. Запустите smoke-тест и
подождите два scrape-интервала; это не означает, что мониторинг сломан.

## Liveness и readiness

`/health` обслуживается event loop без запроса в БД: процесс способен ответить.
`/ready` проверяет соединение и нужную версию схемы, возвращает 503 при отказе.
Для probes есть отдельные два потока, чтобы занятость HTTP workers не блокировала
проверки. Connection timeout — 3 секунды, SQL timeout — 2 секунды для probe.
Docker healthcheck обращается к `/ready`. Nginx закрывает `/ready` и `/metrics`
снаружи; публичный `/health` доступен для smoke.

Правила Prometheus: приложение/БД недоступны минуту, возраст outbox больше минуты,
больше 5% HTTP 5xx, свободно меньше 15% диска. Alertmanager не развёрнут: alerts
видны в Prometheus, внешние сообщения не отправляются. При необходимости открыть
Prometheus без публикации порта:

```bash
cd /opt/gatherly
sudo docker compose -f docker-compose.prod.yml exec -T prometheus \
  wget -qO- http://localhost:9090/api/v1/alerts
```

## Диагностика

На VM, в `/opt/gatherly`:

```bash
sudo docker compose -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.prod.yml logs --tail 100 gatherly postgres nginx
sudo docker compose -f docker-compose.prod.yml exec -T gatherly curl -fsS http://localhost:8080/ready
```

- App down: проверьте exit code, image tag и обязательные environment variables.
- DB down: проверьте контейнер PostgreSQL и диск; readiness должен восстановиться
  после старта БД без рестарта приложения.
- Outbox растёт: проверьте ошибки worker и PostgreSQL. Не переводите записи
  вручную в PROCESSED: это потеряет уведомления.
- Disk almost full: посмотрите backups, Docker images и retention Prometheus.
  Удаляйте только ненужные артефакты; не используйте `down -v` для очистки логов.

## Воспроизводимые проверки

`python3 scripts/smoke.py URL` создаёт три тестовых аккаунта и событие, проверяет
регистрацию, JWT, Unicode/timezones, подписки, конкурентный capacity check,
waitlist, изменение/отмену и уведомления. Данные smoke остаются на стенде.

`scripts/integration.py` работает **только** с изолированным Compose project
`gatherly-check`, конфигурацией `.local/smoke.env` и Nginx на localhost:18080.
Он временно останавливает PostgreSQL, внедряет SQL-ошибки, перезапускает приложение,
проверяет rollback, доставку без дублей, restore и все scrape targets.
Не направляйте этот тест на рабочую БД. Подготовка и запуск полностью описаны
в `.github/workflows/ci.yml`; после теста удаляется только disposable stack.

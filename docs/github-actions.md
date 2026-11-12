# Запуск нагрузочного стенда через GitHub Actions

Workflow: `.github/workflows/load-bench.yml`, имя **Gatherly cloud load benchmark**.
В Actions каждая реплика размещается на собственной VM; Envoy, PostgreSQL,
Prometheus/Grafana и k6 также имеют отдельные VM. Параметр `replicas` задаёт
1–10 VM приложения, итоговое число машин — `replicas + 4`.

## Однократная настройка

Workflow и используемые им Python-файлы, Dockerfiles, миграции, Terraform и Ansible
должны находиться в основной ветке репозитория. Кнопка **Run workflow** появляется,
когда `workflow_dispatch` находится в основной ветке.

В **Settings → Secrets and variables → Actions → Variables** задайте:

| Variable | Значение |
| --- | --- |
| `YC_CLOUD_ID` | ID облака |
| `YC_FOLDER_ID` | ID каталога стенда |
| `BENCH_BUCKET` | Имя приватного бакета фотографий и снимков рекомендаций |
| `BENCH_STATE_BUCKET` | Имя отдельного приватного бакета для Terraform state |
| `BENCH_STAND_NAME` | Необязательно; по умолчанию `gatherly-bench` |
| `YC_ZONE` | Необязательно; по умолчанию `ru-central1-a` |
| `BENCH_LOAD_ZONE` | Необязательно; по умолчанию `ru-central1-d` |
| `BENCH_PREEMPTIBLE` | Необязательно; по умолчанию `true` |

VM имеют 8 vCPU (100%), 16 ГиБ RAM и SSD 20 ГиБ. Квоты должны покрывать выбранное
число машин: для десяти реплик нужны 14 VM, 112 vCPU, 224 ГиБ RAM и 280 ГиБ SSD.
В зоне k6 расходуются ресурсы одной VM, остальные находятся в `YC_ZONE`.

Создайте state-бакет заранее, включите версионирование и выдайте доступ
сервисному аккаунту к чтению, записи и удалению объектов блокировки.
Этот бакет не создаётся и не удаляется Terraform-модулем стенда.

В **Secrets** задайте:

| Secret | Назначение |
| --- | --- |
| `YC_SA_KEY_JSON` | Полный JSON авторизованного ключа сервисного аккаунта: `id`, `service_account_id`, `private_key` |
| `BENCH_STATE_ACCESS_KEY` | Статический access key для state-бакета |
| `BENCH_STATE_SECRET_KEY` | Secret key для state-бакета |
| `BENCH_SSH_PRIVATE_KEY` | Приватный SSH-ключ доступа к VM стенда |
| `BENCH_DB_PASSWORD` | Постоянный пароль БД: 64 шестнадцатеричных символа |
| `BENCH_AUTH_SECRET` | Постоянный секрет JWT и подписанных фото-токенов, не короче 32 байт |

Сервисному аккаунту нужны права управления VM и сетью стенда, бакетом Object Storage,
сервисным аккаунтом доступа к объектам и его статическими ключами. Пароль БД,
секрет JWT и SSH-ключ сохраняются между deploy и update_config. Для уже работающего
стенда используются его прежние значения.

## Перенос существующего локального state

Если стенд уже создавался локально, сначала перенесите его state в общий бакет.
Это позволяет разным GitHub runner управлять теми же VM, сетью и БД.

В локальном окружении Python 3.12 с установленным Terraform:

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

Команда переносит state из `infra/terraform/bench/terraform.tfstate` по ключу
`<BENCH_STAND_NAME>/terraform.tfstate`, сохраняя локальную копию. Первая запись
условная: уже созданный удалённый объект перезаписать нельзя. Повтор с идентичным
state безопасен; отличающийся state, в том числе более новый, приводит к отказу.
Обычный deploy не переключит локальный state на удалённый молча.

Для нового стенда без локального state этот шаг не нужен.

## Первый минутный запуск

Откройте **Actions → Gatherly cloud load benchmark → Run workflow**:

1. `mode`: `deploy_and_test`.
2. `config_file`: `bench/configs/one-minute.json`.
3. `replicas`: нужное число от 1 до 10; пустое поле оставляет значение 3 из файла.
4. Остальные поля оставьте пустыми, чтобы использовать выбранный JSON.

Минутный профиль задаёт 50 RPS ленты, 30 RPS рекомендаций и 5 RPS фото;
загрузка фото начинается на 15-й секунде и длится 45 секунд. По умолчанию без
выбора профиля используется `bench/defaults.json`: 180 секунд, фото с 60-й секунды.

Порядок выполнения: `cleanup_workspace → deploy → load_test`. Итоговый JSON
проверяется до облачных изменений, отображается в summary и передаётся одним
артефактом всем jobs. Поля запуска переопределяют только соответствующие значения
JSON. Неверные параметры завершают запуск до deploy.

При переходе с компактной схемы deploy сохраняет существующую VM БД через
перенос адреса ресурса в Terraform state. Удаление или замена VM БД блокируются.
После готовности отдельных реплик и Envoy прежние контейнеры приложения и
мониторинга удаляются с VM БД, том PostgreSQL сохраняется.

## Изменение работающего стенда

Выберите `mode=update_config`, тот же `config_file` и новые значения `replicas`
или `policy`. Для прогона после изменения включите `run_load_test`.

Используются текущие образы. Terraform добавляет/удаляет только VM приложения;
затем обновляются CDS Envoy и цели Prometheus. PostgreSQL, бакет, Envoy, мониторинг
и k6 сохраняются. Изменения RPS и длительности получает только k6.
Операции одного стенда сериализованы; активный прогон не отменяется новым запуском.

## Результаты

В summary load_test отображаются целевой и успешный RPS, p95, p99, доля ошибок
и dropped iterations для каждого сценария. В артефакте `load-test-results`:

- `config.json` — один итоговый конфиг запуска;
- `report.json` и `summary.json` — сводный и исходный отчёты k6;
- `verification.json` — фактическая политика Envoy и число здоровых реплик;
- `*-logs.txt` — доступная диагностика при ошибке.

Артефакты хранятся 14 дней. Terraform state, SSH-ключи, JSON-ключ сервисного
аккаунта и `private.env` в них не включаются. Отчёт с другим конфигом не публикуется.
Если k6 не создал отчёт, summary указывает на отсутствие результата и диагностику.

Grafana находится на VM `monitoring`; подключение через SSH-туннель:

```bash
ssh -N -L 3000:127.0.0.1:3000 ubuntu@<MONITORING_PUBLIC_IP>
```

Адрес: `http://localhost:3000`, пользователь `admin`, пароль — `BENCH_AUTH_SECRET`.

# Запуск нагрузочного стенда через GitHub Actions

**Русский** | [English](github-actions.en.md)

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
После переключения Terraform может очистить `terraform.tfstate`, сохранив
исходный документ в `terraform.tfstate.backup`. Повторная миграция использует
эту копию только при совпадении настроенного backend с заданными bucket/key.
Порядок записей validation checks не влияет на сравнение; изменения данных,
lineage, serial или статуса проверки по-прежнему приводят к отказу.

Для нового стенда без локального state этот шаг не нужен.

### Загрузка настроек через GitHub CLI

Вход выполняется локально; токен не нужно передавать в чат или сохранять в Git:

```bash
GH_CONFIG_DIR="$PWD/.local/gh" .local/bin/gh auth login \
  --hostname github.com --git-protocol https --web --scopes repo,workflow
export GH_CONFIG_DIR="$PWD/.local/gh"
```

Можно использовать установленный системный `gh`. Локальная копия `.local/bin/gh`
не включается в репозиторий. Для fine-grained PAT нужны права записи Actions,
Secrets и Variables на Gatherly; для изменения workflow также Contents/Workflows.
Точные требования к Secrets описаны в [GitHub REST API](https://docs.github.com/en/rest/actions/secrets).

Утилита `python -m bench.github_setup` принимает приватный JSON с тремя полями:
`repository` (например, `dmgoldberg1/Gatherly`), `variables` и `secrets`.
Два последних поля — объекты с именами настроек из таблиц выше и строковыми
значениями. Все шесть secrets обязательны. Для существующего стенда берите
пароль БД и auth secret из его прежнего окружения. Создайте отдельный SSH-ключ
Actions и добавьте его публичную часть в `authorized_keys` существующих VM;
проверьте вход этим ключом перед запуском. Личный SSH-ключ в Actions не загружайте.

Храните JSON под `.local/bench/actions/`, права каталога `700`, файла `600`.
После подготовки облачного аккаунта и ключей:

```bash
chmod 600 .local/bench/actions/github-settings.json
python -m bench.github_setup validate --settings .local/bench/actions/github-settings.json
python -m bench.github_setup configure --settings .local/bench/actions/github-settings.json
python -m bench.github_setup check --settings .local/bench/actions/github-settings.json
```

`configure` проверяет доступ к API и отсутствие активного benchmark workflow до
первого изменения. Секреты передаются `gh secret set` через stdin, значения не
печатаются и не попадают в аргументы процессов. `check` сверяет значения variables
и наличие всех шести имён secrets: прочитать значения secrets GitHub не позволяет.

### Права CI для уже существующего стенда

Когда сеть, бакет фотографий и аккаунт `objects` уже созданы, CI не требуется
администрирование IAM всего каталога. Подготовленный набор прав:

| Область | Роли |
| --- | --- |
| Каталог стенда | `compute.editor`, `vpc.user`, `vpc.publicAdmin`, `vpc.securityGroups.admin` |
| Только существующий аккаунт `objects` | `viewer`, `iam.serviceAccounts.accessKeyAdmin` |
| Только бакет фотографий | `storage.admin` |
| Только бакет state | `storage.editor` |

Создание CI-аккаунта, авторизованного и статического ключей выполняет оператор.
Права на каталог позволяют CI создавать/удалять VM и менять сетевой доступ
ресурсов этого каталога; используйте каталог, выделенный под стенд. Такой аккаунт
предназначен для обновления существующей инфраструктуры, а не для создания нового
стенда с нуля. Настройку ролей на отдельные бакеты описывает
[документация Yandex Cloud](https://yandex.cloud/ru/docs/storage/operations/buckets/iam-access).

State можно предварительно скопировать с текущим IAM-токеном оператора через
`bench.backend.seed(..., client=...)`, не создавая постоянный ключ.
Функция использует условную запись и проверяет документ обратным чтением;
отличающийся удалённый state не перезаписывает. Это подготовленная копия,
локальный backend остаётся активным. После выдачи ключей выполните обычный
`migrate_state` из инструкции выше, чтобы переключить backend на общий бакет.
Не запускайте параллельное управление стендом через старый локальный backend.

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

Первый запуск с одной отдельной репликой через CLI, после настройки credentials,
переключения backend и проверки квот:

```bash
.local/bin/gh workflow run load-bench.yml --repo dmgoldberg1/Gatherly --ref main \
  -f mode=deploy_and_test -f config_file=bench/configs/one-minute.json -f replicas=1
.local/bin/gh run list --repo dmgoldberg1/Gatherly --workflow load-bench.yml --limit 5
.local/bin/gh run watch <RUN_ID> --repo dmgoldberg1/Gatherly --exit-status
```

Для `replicas=1` нужны 64 ГиБ RAM в `YC_ZONE` (БД, Envoy, мониторинг, приложение)
и ещё 16 ГиБ в зоне k6: всего 5 VM, 40 vCPU, 80 ГиБ RAM, 100 ГиБ SSD, 5 IP.
Существующие VM учитываются в этих суммах. Проверьте доступный остаток с учётом
остальных ресурсов облака до сборки образов и deploy. Подготовленная копия state
и наличие workflow ещё не означают, что распределённый прогон выполнен.

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

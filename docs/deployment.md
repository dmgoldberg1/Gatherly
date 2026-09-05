# Запуск и развёртывание

## Локально

Нужны Docker Engine и Compose v2. Из корня репозитория:

```bash
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8080/ready
python3 scripts/smoke.py http://127.0.0.1:8080
```

Приложение: http://localhost:8080. `docker compose --profile docs up -d` добавляет
Swagger UI на http://localhost:8081. PostgreSQL не публикует host port.
Пароль локальной БД — `gatherly`. Для сохранения сессий после рестарта создайте
`.env` с `GATHERLY_AUTH_SECRET` по [инструкции](authentication.md).

Остановка: `docker compose down`. Данные сохраняются в volume; `down -v` удаляет их.
Миграции применяются автоматически перед запуском приложения и к уже существующему
volume. Не удаляйте старую базу ради обновления схемы.

## Yandex Cloud

Минимальная конфигурация: 2 vCPU с гарантированной долей 20%, 2 ГБ RAM,
20 ГБ network-hdd, Ubuntu 24.04, статический IPv4. Образ собирается вне VM.
CPU/RAM оплачиваются во время работы; диск и зарезервированный IP могут
оплачиваться и после остановки. Проверяйте актуальную стоимость в
[тарифах Compute](https://yandex.cloud/ru/docs/compute/pricing) и
[тарифах VPC](https://yandex.cloud/ru/docs/vpc/pricing).

Нужны настроенный `yc`, Terraform 1.16.x, SSH-ключ и права на создание ресурсов
в выбранном folder. Токен передаётся через environment:

```bash
export YC_TOKEN="$(yc iam create-token)"
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# Заполните cloud_id, folder_id, ssh_public_key_path и admin_cidr (ваш IPv4/32).
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform plan -out=gatherly.tfplan
terraform -chdir=infra/terraform apply gatherly.tfplan
terraform -chdir=infra/terraform output
```

Если квота VPC исчерпана, задайте `existing_subnet_id` подсети в выбранной zone.
Terraform создаст собственную security group, VM и IP; существующая сеть не
импортируется в state и не удаляется при destroy. State и tfvars исключены из Git.
Сохраните state локально в надёжном месте — это учёт созданных ресурсов.

Входящие 80/443 разрешены всем; 22 — только `admin_cidr`. Приложение, БД,
Prometheus и exporters не имеют опубликованных портов. Grafana слушает
`127.0.0.1:3000` на VM. При смене административного IP обновите tfvars и apply.

## Ansible

Нужен Ansible Core; playbook использует только встроенные модули, две роли.
Дождитесь доступности SSH на выданном IP. Сверьте fingerprint SSH сервера при
первом подключении и сохраните его в known_hosts.

```bash
cp infra/ansible/inventory.ini.example infra/ansible/inventory.ini
cp infra/ansible/secrets.yml.example infra/ansible/secrets.yml
chmod 600 infra/ansible/secrets.yml
# В inventory укажите public_ip; в secrets — image и три независимых секрета.
openssl rand -hex 32  # повторите отдельно для каждого секрета
ANSIBLE_CONFIG=infra/ansible/ansible.cfg ansible-playbook -i infra/ansible/inventory.ini infra/ansible/deploy.yml \
  -e @infra/ansible/secrets.yml
```

Пароль БД должен быть 64 hex символами, чтобы DSN не требовал URL-экранирования.
Не меняйте его только в `.env` существующего volume: PostgreSQL устанавливает
POSTGRES_PASSWORD при первой инициализации. Аналогично пароль администратора
Grafana задаётся при создании её БД, а не при каждом рестарте.

Первое развёртывание можно сделать без registry:

```bash
docker build -t gatherly:demo .
mkdir -p .local
docker save gatherly:demo -o .local/gatherly.tar
```

Укажите `gatherly_image: gatherly:demo` и `image_archive` с абсолютным путём к архиву
в secrets.yml. Ansible загрузит образ через SSH. Для следующих релизов используйте
неизменяемые теги `ghcr.io/<owner>/gatherly:sha-<commit>` и уберите `image_archive`.
GHCR package должен быть публичным, либо предварительно выполните `docker login`
на VM от root. Credentials registry в репозиторий не добавляются.

Playbook устанавливает Docker, копирует стек в `/opt/gatherly`, запускает БД,
миграции, приложение и мониторинг, проверяет readiness и Grafana. Повторный запуск
не пересоздаёт volumes. Healthcheck показывает деградацию; Docker не перезапускает
контейнер автоматически только потому, что тот стал unhealthy.

## Домен и HTTPS

Для бесплатной демонстрации используем `meet-greet.<PUBLIC_IP>.sslip.io`.
[sslip.io](https://github.com/cunnie/sslip.io) автоматически разрешает имя во встроенный
IP; аккаунт регистратора, покупка домена и ручная A-запись не нужны. Статический
IP сохраняет адрес после перезапуска VM. Это сторонний бесплатный DNS-сервис.

Укажите в secrets.yml `domain` с этим именем и `enable_https: true`, затем повторите
Ansible. Email необязателен: при пустом `letsencrypt_email` Certbot регистрирует ACME
аккаунт без email. Контроль renewal выполняется cron и ручным dry-run.

Если позже нужен собственный `.ru`, зарегистрируйте его у выбранного регистратора.
После покупки создайте DNS-запись `A` для `@` → Terraform `public_ip`.
Удалите ошибочную AAAA-запись, если IPv6 не настроен. Дождитесь разрешения имени.
В secrets.yml добавьте:

```yaml
domain: meet-greet.<PUBLIC_IP>.sslip.io
letsencrypt_email: "" # необязательно
enable_https: true
```

Повторите Ansible. Certbot подтвердит домен через HTTP webroot, Nginx переключится
на HTTPS и редирект 80→443. Cron запускает renewal дважды в день и reload Nginx
после успешного обновления. Проверка renewal на VM:

```bash
sudo certbot renew --dry-run --no-random-sleep-on-renew --config-dir /opt/gatherly/letsencrypt
curl --fail https://YOUR_DOMAIN/health
```

До выпуска сертификата доступен HTTP/IP. HTTPS становится готовым только после
успешного выпуска сертификата; для sslip.io отдельная покупка домена не нужна.

## Обновление, rollback, backup

Перед обновлением на VM:

```bash
cd /opt/gatherly
sudo sh scripts/backup.sh
```

Ежедневный cron хранит дампы в `/opt/gatherly/backups` семь дней. Заберите дамп
через SSH на другую машину. Для проверки восстановления в отдельную пустую БД:

```bash
sudo docker compose -f docker-compose.prod.yml exec -T postgres createdb -U gatherly gatherly_restore_check
sudo docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_restore -U gatherly -d gatherly_restore_check < backups/CHOSEN.dump
sudo docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U gatherly -d gatherly_restore_check -c 'SELECT count(*) FROM users;'
```

Для восстановления основной БД сначала остановите приложение и сделайте текущий
дамп. Восстанавливайте в новую БД и переключайте приложение после проверки;
не запускайте очистку существующей БД вслепую. Интеграционный тест автоматически
проверяет pg_dump/pg_restore в отдельной временной БД.

Rollback приложения: верните предыдущий `gatherly_image` в secrets.yml и повторите
Ansible. Это работает только с совместимой схемой; текущие миграции добавляют
служебную таблицу и исправляют sequences, не удаляя бизнес-данных.

## CI и расходы

GitHub Actions собирает C++, выполняет unit и fault-injection tests, проверяет
Terraform/Ansible/JavaScript. Только успешный push в main публикует GHCR образ
с SHA коммита. Deployment запускается вручную через Ansible.
Сохраните GitHub workflow в репозитории и push, чтобы запустить CI; локальная
проверка не означает, что workflow уже выполнялся в GitHub.

Остановить VM после демонстрации:

```bash
yc compute instance stop "$(terraform -chdir=infra/terraform output -raw instance_id)"
```

Повторный start сохраняет диск и статический IP. Полностью прекратить расходы
на ресурсы Gatherly — `terraform -chdir=infra/terraform destroy` после копирования
нужных данных: эта команда удаляет VM, её диск и IP. Общая default-подсеть при
использовании `existing_subnet_id` остаётся нетронутой.

## Проверено 5 сентября 2026

- Native CMake/CTest и Docker build: 38 тестов проходят.
- Локальный Compose стартует с новой БД и выполняет HTTP smoke.
- Production Compose: SQL fault injection, rollback, outbox/restart, восстановление
  PostgreSQL, pg_dump/restore и четыре scrape targets проходят проверки.
- Terraform применён в Yandex Cloud; публичный HTTPS smoke и браузерные действия
  проходят. Grafana через SSH показывает реальные series.
- Повторный Ansible playbook: changed=0, failed=0.
- Certbot renewal dry-run успешно продлил сертификат в staging; первый backup
  публичной БД создан. Снаружи доступны 80/443, сервисные порты закрыты.
- GitHub Actions workflow добавлен; запуск на GitHub требует push в репозиторий.

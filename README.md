# Gatherly

Платформа для организации неформальных встреч: настолки, футбол,
кино и встречи с друзьями. Пользователи создают события, подписываются друг
на друга, занимают места и получают уведомления.

Сервисом можно пользоваться через веб-интерфейс или HTTP API. Доступны запуск
на своём компьютере и развёртывание в Yandex Cloud с HTTPS и мониторингом.

Демо: https://meet-greet.158.160.194.229.sslip.io — доступно, когда VM запущена.

## Возможности

- регистрация и вход;
- профили, подписки, публичная лента и лента по подпискам;
- создание, редактирование, публикация и отмена событий;
- запись на события, отмена участия и очередь ожидания, если места закончились;
- просмотр своих событий и истории участий;
- уведомления о событиях и изменениях участия;
- просмотр доступности сервиса, числа запросов и нагрузки на сервер в Grafana.

## Сборка и запуск через Docker

Требуются Docker Engine и Docker Compose v2. Команды выполняются из корня
репозитория:

```bash
git clone https://github.com/dmgoldberg1/Gatherly.git
cd Gatherly

docker compose up --build -d --wait
docker compose ps
```

Приложение доступно на http://localhost:8080.

Проверка API и запуск Swagger UI:

```bash
python3 scripts/smoke.py http://localhost:8080
docker compose --profile docs up -d swagger-ui
```

Swagger UI доступен на http://localhost:8081. Smoke-тест создаёт тестовых
пользователей и проверяет основной сценарий работы с событием.
Для сохранения сессий после перезапуска задайте в `.env` постоянный
`GATHERLY_AUTH_SECRET`, сгенерированный через `openssl rand -hex 32`.

Остановка: `docker compose down`. Данные сохраняются между запусками.
Мониторинг доступен при развёртывании на VM.

## Локальная сборка через CMake

Требуются CMake 3.22+, компилятор C++23, OpenSSL headers и доступ в интернет
для загрузки зависимостей. Для HTTP-сервера также нужны Drogon, libpqxx,
JsonCpp и pkg-config. Для сборки и запуска только тестов:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Для сборки сервера:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DGATHERLY_REQUIRE_DROGON=ON -DGATHERLY_REQUIRE_POSTGRES=ON
cmake --build build --parallel 2
```

Запустите и подготовьте базу данных, затем запустите приложение:

```bash
docker run -d --name gatherly-native-db \
  -p 127.0.0.1:15432:5432 \
  -e POSTGRES_USER=gatherly -e POSTGRES_PASSWORD=gatherly -e POSTGRES_DB=gatherly \
  -v gatherly_native_data:/var/lib/postgresql/data \
  -v "$PWD/db/migrations:/migrations:ro" \
  -v "$PWD/scripts/migrate.sh:/migrate.sh:ro" postgres:16-alpine

until docker exec gatherly-native-db pg_isready -h 127.0.0.1 -U gatherly -d gatherly; do sleep 1; done
docker exec -e PGUSER=gatherly -e PGDATABASE=gatherly gatherly-native-db sh /migrate.sh

GATHERLY_DATABASE_URL=postgresql://gatherly:gatherly@127.0.0.1:15432/gatherly \
GATHERLY_AUTH_SECRET="$(openssl rand -hex 32)" \
  ./build/gatherly_drogon
```

Команды выполняются из корня репозитория.
Интерфейс доступен на http://localhost:8080; этот порт должен быть свободен.
Остановка сервера — `Ctrl+C`, БД — `docker stop gatherly-native-db`.
При следующем запуске БД используйте `docker start gatherly-native-db`.

## Развёртывание в Yandex Cloud

После развёртывания приложение будет доступно по HTTPS, а мониторинг — в Grafana.
По умолчанию используется VM с 2 vCPU, долей CPU 20%, 2 ГБ RAM и диском 20 ГБ.

Требуются настроенный Yandex Cloud CLI (`yc`), Terraform 1.16.x, Ansible Core,
Docker и SSH-ключ. Команды выполняются локально из корня репозитория.

### 1. Создать VM

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
```

В `terraform.tfvars` заполните `cloud_id`, `folder_id`, `ssh_public_key_path`
и `admin_cidr` — свой публичный IPv4 с маской `/32`. При использовании готовой
подсети укажите `existing_subnet_id` в выбранной зоне.

```bash
export YC_TOKEN="$(yc iam create-token)"
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform plan
terraform -chdir=infra/terraform apply
terraform -chdir=infra/terraform output
```

Сохраните `public_ip` из вывода и проверьте подключение: `ssh ubuntu@<PUBLIC_IP>`.
После проверки выйдите с VM.

### 2. Развернуть приложение

Соберите образ локально для передачи на VM:

```bash
docker build -t gatherly:demo .
mkdir -p .local
docker save gatherly:demo -o .local/gatherly.tar

cp infra/ansible/inventory.ini.example infra/ansible/inventory.ini
cp infra/ansible/secrets.yml.example infra/ansible/secrets.yml
chmod 600 infra/ansible/secrets.yml
```

Копируйте примеры только при первой настройке. В `inventory.ini` укажите IP VM
и путь к SSH-ключу. В `secrets.yml` задайте:

- `gatherly_image: gatherly:demo`;
- `image_archive`: абсолютный путь к `.local/gatherly.tar`;
- `postgres_password`, `auth_secret`, `grafana_admin_password`: три разных
  значения, каждое сгенерированное через `openssl rand -hex 32`;
- `domain: meet-greet.<PUBLIC_IP>.sslip.io` и `enable_https: true` для HTTPS.

HTTPS настраивается автоматически; email для сертификата можно указать в `letsencrypt_email`.

```bash
ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
  ansible-playbook -i infra/ansible/inventory.ini infra/ansible/deploy.yml \
  -e @infra/ansible/secrets.yml

python3 scripts/smoke.py 'https://meet-greet.<PUBLIC_IP>.sslip.io'
```

Замените `<PUBLIC_IP>` на адрес VM. После развёртывания приложение доступно
по этому HTTPS-адресу. Для обновления можно указать опубликованный CI образ
`ghcr.io/<owner>/gatherly:sha-<commit>`, убрать `image_archive` и повторить Ansible.
Образ должен быть публичным либо доступным после `sudo docker login ghcr.io` на VM.

### 3. Открыть Grafana и управлять VM

Grafana доступна через SSH-туннель:

```bash
ssh -N -L 3000:127.0.0.1:3000 'ubuntu@<PUBLIC_IP>'
```

Откройте http://localhost:3000, войдите как `admin` с паролем
`grafana_admin_password` из `secrets.yml`. Дашборд: **Gatherly — service health**.
Выберите **Last 30 minutes** и обновление каждые **15 секунд**.

Проверить контейнеры и логи на VM:

```bash
cd /opt/gatherly
sudo docker compose -f docker-compose.prod.yml ps -a
sudo docker compose -f docker-compose.prod.yml logs --tail=100 gatherly prometheus
```

Остановить VM после демонстрации и запустить снова — с локального компьютера:

```bash
yc compute instance stop "$(terraform -chdir=infra/terraform output -raw instance_id)"
yc compute instance start "$(terraform -chdir=infra/terraform output -raw instance_id)"
```

Диск и статический IP сохраняются. Terraform state нужен для дальнейшего
управления инфраструктурой — сохраните его вместе с локальными настройками.

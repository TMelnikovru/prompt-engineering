# Развёртывание на docker-prod

Приложение и отдельный PostgreSQL 17 запускаются через Docker Compose.
Каталог сервера: `/home/tmelnikovru/apps/hse-study-agent`.
Приложение доступно для настраиваемого отдельно прокси по
`http://192.168.7.197:8000`; проверка: `/api/v1/health`.
PostgreSQL не публикует порт на хосте. Данные находятся в Docker volume
`hse-study-agent_postgres_data` и сохраняются при пересоздании контейнеров.

Это развёртывание текущего каркаса. Оно не добавляет живые источники,
сохранение заданий или фоновые напоминания. Учебные данные на сервере отключены.

## Автоматическое и ручное обновление

Cron пользователя `tmelnikovru` проверяет `main` каждую минуту, в том числе
после перезагрузки и без открытой SSH-сессии. Приватный репозиторий читается
отдельным SSH deploy key с правом только чтения. GitHub runners не требуют
доступа к локальной сети сервера. Новый commit собирается в отдельный образ;
после запуска проверяются HTTP healthcheck, главная страница и SQL-запрос
из контейнера приложения. При ошибке сборки продолжает работать прежний образ;
при ошибке запуска или проверок выполняется попытка возврата к предыдущему.
Обновление имеет короткий перерыв на пересоздание приложения.
Cron не ожидает GitHub Actions: проверки CI должны проходить до слияния в `main`.
CI также запускает настоящий deploy-скрипт в отдельном Compose-проекте:
сборка, запуск от непривилегированного пользователя, HTTP и SQL-проверки.

```sh
ssh docker-prod
ROOT="$HOME/apps/hse-study-agent"
bash "$ROOT/deploy.sh" "$ROOT"
tail -n 100 "$ROOT/deploy.log"
crontab -l
```

Одинаковый commit повторно не разворачивается, параллельные запуски исключены
через `flock`. Для применения изменённого `.env`:

```sh
HSE_FORCE_DEPLOY=1 bash "$ROOT/deploy.sh" "$ROOT"
```

Compose и диагностика текущей версии:

```sh
cd "$ROOT/current"
export HSE_APP_IMAGE="hse-study-agent:$(basename "$(readlink -f "$ROOT/current")")"
docker compose --project-name hse-study-agent --env-file "$ROOT/.env" ps
docker compose --project-name hse-study-agent --env-file "$ROOT/.env" logs --tail 100 app
docker compose --project-name hse-study-agent --env-file "$ROOT/.env" exec postgres \
  sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

В приложении установлен `psycopg`; соединение использует `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, `PGPASSWORD` из окружения: `psycopg.connect()`.
В серверном `.env` также подготовлен `HSE_DATABASE_URL` для модуля хранения;
при изменении учётных данных его нужно обновлять вместе с параметрами PostgreSQL.
Миграций и таблиц приложения пока нет. `/api/v1/health` проверяет HTTP,
а SQL-проверка выполняется отдельно при развёртывании.

## Остановка обновлений и откат

```sh
# Сначала остановить обновления, иначе main снова заменит выбранную версию.
crontab -l | sed '/# hse-study-agent-autodeploy$/d' | crontab -
bash "$ROOT/deploy.sh" "$ROOT" "$(cat "$ROOT/previous")"
# Снова включить автоматическое обновление:
bash "$ROOT/current/deploy/install-cron.sh" "$ROOT"
```

Откат не меняет содержимое БД. Будущие миграции должны учитывать совместимость.
Команды `docker compose down -v` и `docker volume rm` удаляют данные;
для обычного обновления они не нужны. Пароль существующего PostgreSQL нельзя
сменить только редактированием `.env`: требуется также изменить пароль роли в БД.
Не печатайте `.env`, `docker inspect` с окружением или `docker compose config`
без `--quiet`: они содержат секреты.

## Резервная копия БД

После настройки `ROOT` и `HSE_APP_IMAGE`, как выше:

```sh
umask 077
mkdir -p "$ROOT/backups"
docker compose --project-name hse-study-agent --env-file "$ROOT/.env" exec -T postgres \
  sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "$ROOT/backups/database-$(date +%Y%m%d-%H%M%S).dump"
```

Автоматические бэкапы и вынос копий с сервера пока не настроены.
Образы и каталоги версий сохраняются для отката; при регулярной разработке
нужно контролировать свободное место и удалять старые версии выборочно.
Не запускайте глобальную очистку Docker на общем сервере.

## Повторная установка

Нужны Linux, работающий Docker с Compose v2.20+, Git, OpenSSH, Python 3,
`flock` и запущенный cron. Пользователь должен иметь доступ к Docker.
Скрипты можно передать с рабочей машины через `scp`.

1. Запустить `bash deploy/prepare.sh`. Он создаёт каталог с правами 700,
   случайный пароль в `.env` с правами 600 и отдельную SSH-пару ключей.
   Существующие секреты не перезаписываются.
2. Добавить выведенный **публичный** ключ в GitHub → репозиторий
   `TMelnikovru/prompt-engineering` → Settings → Deploy keys, без права записи.
3. Создать `$ROOT/known_hosts` с ключами GitHub из официального API
   `https://api.github.com/meta` (`ssh_keys`), с именем `[ssh.github.com]:443`.
   Это позволяет читать GitHub по SSH через порт 443 с проверкой ключа хоста.
4. Клонировать репозиторий и сохранить SSH-настройки:

```sh
ROOT="$HOME/apps/hse-study-agent"
SSH_COMMAND="ssh -i $ROOT/github_ed25519 -o IdentitiesOnly=yes -o UserKnownHostsFile=$ROOT/known_hosts -o StrictHostKeyChecking=yes -o BatchMode=yes -o ConnectTimeout=15"
GIT_SSH_COMMAND="$SSH_COMMAND" git clone --branch main --single-branch \
  ssh://git@ssh.github.com:443/TMelnikovru/prompt-engineering.git "$ROOT/repository"
git -C "$ROOT/repository" config core.sshCommand "$SSH_COMMAND"
install -m 700 "$ROOT/repository/deploy/deploy.sh" "$ROOT/deploy.sh"
```

5. Отредактировать `$ROOT/.env`: для текущего сервера
   `HSE_BIND_HOST=192.168.7.197`, `HSE_PORT=8000`.
   По умолчанию используется loopback. При прокси на другом хосте задайте
   адрес сервера, доступный прокси. Ключи OpenAI остаются только на сервере;
   конкретная модель не выбрана.
6. Выполнить первый deploy, затем установить cron:

```sh
bash "$ROOT/deploy.sh" "$ROOT"
bash "$ROOT/current/deploy/install-cron.sh" "$ROOT"
```

Скрипт `$ROOT/deploy.sh` устанавливается отдельно и автоматически не заменяется.
После изменения логики deploy его нужно обновить вручную через `install`, как выше.
Сам контейнер приложения и конфигурация Compose берутся из нового commit `main`.

## Preview ветки backend/postgresql

Второй сайт работает на `docker-prod` по `http://192.168.7.197:8001`.
Для `hsep.tmelnikov.ru` укажите этот адрес как upstream прокси.
Каталог: `/home/tmelnikovru/apps/hse-study-agent-postgresql`.
Compose project — `hse-study-agent-postgresql`; у него отдельные контейнеры,
сеть, пароль и volume `hse-study-agent-postgresql_postgres_data`.
Основной сайт остаётся на порту 8000.

```sh
ROOT="$HOME/apps/hse-study-agent-postgresql"
```

В `$ROOT/deploy.env` сохранены настройки самого deploy-скрипта:

```sh
HSE_DEPLOY_BRANCH=backend/postgresql
HSE_DEPLOY_PROJECT=hse-study-agent-postgresql
```

Это доверенный shell-файл настроек сервера; секреты приложения находятся в
отдельном `$ROOT/.env` с правами 600. Cron проверяет выбранную ветку каждую
минуту. У каждого Compose project своя запись cron; переустановка preview
не удаляет запись основного сайта. Для другой ветки создайте отдельный каталог,
уникальный Compose project, порт и серверный `.env`.

При наличии `alembic.ini` deploy сначала запускает PostgreSQL, затем
`python -m alembic upgrade head` в новом образе, после чего обновляет приложение.
Дополнительно проверяется `/api/v1/ready`. Миграции относятся только к БД
выбранного окружения. Автоматический откат контейнера не откатывает схему БД.

В этой ветке Compose также запускает `wiki-worker`: он автоматически загружает
вики, извлекает данные и обновляет сводку, повторяя работу после отказа источника.
Его состояние и логи: `docker compose ... ps` и `docker compose ... logs wiki-worker`.
Обычный интервал — 15 минут, задержки повторов и охват описаны в `docs/WIKI.md`.
Обновлённый deploy удаляет orphan-контейнеры только своего Compose project,
чтобы при откате к версии без worker тот не продолжал работать со старым сайтом.

```sh
bash "$ROOT/deploy.sh" "$ROOT"
tail -n 100 "$ROOT/deploy.log"
cd "$ROOT/current"
export HSE_APP_IMAGE="hse-study-agent-postgresql:$(basename "$(readlink -f "$ROOT/current")")"
docker compose --project-name hse-study-agent-postgresql --env-file "$ROOT/.env" ps
docker compose --project-name hse-study-agent-postgresql --env-file "$ROOT/.env" exec -T app \
  python -m alembic current
curl --fail http://192.168.7.197:8001/api/v1/ready
```

Настройка канала обновления и повторное включение cron используют
`deploy.env`, поэтому аргумент ветки при обычном ручном запуске не нужен.
Для однократного развёртывания можно указать ветку либо полный SHA вторым
аргументом: `bash "$ROOT/deploy.sh" "$ROOT" backend/postgresql`.

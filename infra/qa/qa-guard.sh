#!/bin/sh
set -eu

fail() {
    printf '%s\n' "QA safety validation failed: $1" >&2
    exit 64
}

require_hex_secret() {
    label=$1
    value=$2
    minimum_length=$3
    if [ "${#value}" -lt "$minimum_length" ]; then
        fail "$label is missing or too short"
    fi
    case "$value" in
        *[!0123456789abcdefABCDEF]*) fail "$label must contain only hexadecimal characters" ;;
    esac
    unique_characters=$(printf '%s' "$value" | fold -w 1 | sort -u | wc -l)
    [ "$unique_characters" -ge 8 ] || fail "$label does not have sufficient character diversity"
}

mode=${1:-}
if [ "$#" -eq 0 ]; then
    fail "Guard mode is required"
fi
shift
case "$mode" in
    config|api|migrate|bootstrap|reset_admin|migrate_admin_email|api_test|ui_test|qa_unit_test|qa_admission_migrate|qa_admission_mysql_test) ;;
    *) fail "Unknown QA safety-check mode" ;;
esac

validate_database_target() {
    [ "${QA_DATABASE_NAME:-}" = "nexus_qa_test" ] || fail "QA_DATABASE_NAME must be nexus_qa_test"
    [ "${QA_DATABASE_HOST:-}" = "qa-mysql" ] || fail "QA_DATABASE_HOST must be qa-mysql"
    [ "${QA_DATABASE_USER:-}" = "nexus_qa_runner" ] || fail "QA_DATABASE_USER must be nexus_qa_runner"
    password=${QA_DATABASE_PASSWORD:-}
    require_hex_secret QA_DATABASE_PASSWORD "$password" 64
    expected_url="mysql+pymysql://nexus_qa_runner:${password}@qa-mysql:3306/nexus_qa_test"
    [ "${DATABASE_URL:-}" = "$expected_url" ] || fail "DATABASE_URL must target the isolated QA database"
}

validate_admission_test_project() {
    expected_project=nexus-qa-admission-test
    [ "${QA_ADMISSION_TEST_PROJECT:-}" = "$expected_project" ] || fail "QA admission tests require the dedicated disposable project marker"
    [ "${QA_ADMISSION_COMPOSE_PROJECT:-}" = "$expected_project" ] || fail "QA admission tests refuse persistent QA or development Compose projects"
    [ "${QA_ADMISSION_DB_STORAGE:-}" = "tmpfs" ] || fail "QA admission MySQL must use disposable tmpfs storage"
}

if [ "$mode" = "config" ]; then
    validate_database_target
    require_hex_secret QA_MYSQL_ROOT_PASSWORD "${QA_MYSQL_ROOT_PASSWORD:-}" 64
    require_hex_secret QA_JWT_SECRET_KEY "${QA_JWT_SECRET_KEY:-}" 64
    require_hex_secret QA_ADMIN_PASSWORD "${QA_ADMIN_PASSWORD:-}" 64
    require_hex_secret QA_RBAC_PASSWORD "${QA_RBAC_PASSWORD:-}" 64
    [ "$QA_DATABASE_PASSWORD" != "$QA_MYSQL_ROOT_PASSWORD" ] || fail "QA database and root secrets must differ"
    [ "$QA_DATABASE_PASSWORD" != "$QA_JWT_SECRET_KEY" ] || fail "QA database and JWT secrets must differ"
    [ "$QA_ADMIN_PASSWORD" != "$QA_RBAC_PASSWORD" ] || fail "QA administrator and RBAC test passwords must differ"
    [ "$QA_ADMIN_PASSWORD" != "$QA_DATABASE_PASSWORD" ] && [ "$QA_ADMIN_PASSWORD" != "$QA_MYSQL_ROOT_PASSWORD" ] && [ "$QA_ADMIN_PASSWORD" != "$QA_JWT_SECRET_KEY" ] || fail "QA administrator password must be unique"
    [ "$QA_RBAC_PASSWORD" != "$QA_DATABASE_PASSWORD" ] && [ "$QA_RBAC_PASSWORD" != "$QA_MYSQL_ROOT_PASSWORD" ] && [ "$QA_RBAC_PASSWORD" != "$QA_JWT_SECRET_KEY" ] || fail "QA RBAC test password must be unique"
    [ "$QA_MYSQL_ROOT_PASSWORD" != "$QA_JWT_SECRET_KEY" ] || fail "QA root and JWT secrets must differ"
    exit 0
fi

if [ "$mode" = "ui_test" ]; then
    [ "${QA_DATABASE_NAME:-}" = "nexus_qa_test" ] || fail "QA_DATABASE_NAME must be nexus_qa_test"
    [ "${QA_DATABASE_HOST:-}" = "qa-mysql" ] || fail "QA_DATABASE_HOST must be qa-mysql"
    [ "${NEXUS_QA_MODE:-}" = "true" ] || fail "UI tests must enable NEXUS_QA_MODE=true"
    [ "${BASE_URL:-}" = "http://proxy" ] || fail "UI tests must target the QA proxy"
    [ -z "${DATABASE_URL:-}" ] && [ -z "${MYSQL_TEST_DATABASE_URL:-}" ] || fail "UI tests must not receive database connection URLs"
    [ -z "${DB_HOST:-}" ] && [ -z "${DB_USER:-}" ] && [ -z "${DB_PASSWORD:-}" ] && [ -z "${DB_NAME:-}" ] || fail "UI tests must not receive direct database settings"
    [ -z "${QA_DATABASE_USER:-}" ] && [ -z "${QA_DATABASE_PASSWORD:-}" ] || fail "UI tests must not receive database credentials"
    [ "${TEST_USER_EMAIL:-}" = "${QA_ADMIN_EMAIL:-}" ] || fail "UI tests must use the QA administrator account"
    require_hex_secret TEST_USER_PASSWORD "${TEST_USER_PASSWORD:-}" 64
    exec npx playwright test --config=/tests/playwright.config.ts
fi

if [ "$mode" = "qa_unit_test" ]; then
    [ "${APP_ENV:-}" = "test" ] || fail "Backend unit tests must use APP_ENV=test"
    [ "${DATABASE_URL:-}" = "sqlite+pysqlite:///:memory:" ] || fail "Backend unit tests must use in-memory SQLite"
    [ -z "${QA_DATABASE_PASSWORD:-}" ] && [ -z "${DB_PASSWORD:-}" ] && [ -z "${MYSQL_TEST_DATABASE_URL:-}" ] && [ -z "${MYSQL_TEST_DATABASE_KIND:-}" ] || fail "Backend unit tests must not receive MySQL credentials or URLs"
    [ -z "${DB_HOST:-}" ] && [ -z "${DB_USER:-}" ] && [ -z "${DB_NAME:-}" ] && [ -z "${QA_DATABASE_HOST:-}" ] && [ -z "${QA_DATABASE_USER:-}" ] && [ -z "${QA_DATABASE_NAME:-}" ] || fail "Backend unit tests must not receive MySQL connection settings"
    exec python -m pytest tests/test_reset_qa_admin.py tests/test_migrate_qa_admin_email.py tests/test_auth_login.py tests/test_qa_execution_admissions.py -v -p no:cacheprovider
fi

validate_database_target

case "$mode" in
    api)
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${APP_ENV:-}" = "test" ] || fail "QA application commands must use APP_ENV=test"
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000
        ;;
    migrate)
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${APP_ENV:-}" = "test" ] || fail "QA application commands must use APP_ENV=test"
        exec alembic upgrade head
        ;;
    bootstrap)
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${APP_ENV:-}" = "test" ] || fail "QA application commands must use APP_ENV=test"
        [ -n "${QA_ADMIN_EMAIL:-}" ] && [ -n "${QA_ADMIN_NAME:-}" ] || fail "QA administrator identity is required"
        exec python -m app.commands.bootstrap_admin --email "$QA_ADMIN_EMAIL" --name "$QA_ADMIN_NAME"
        ;;
    reset_admin)
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${APP_ENV:-}" = "test" ] || fail "QA application commands must use APP_ENV=test"
        [ -n "${QA_ADMIN_EMAIL:-}" ] || fail "QA_ADMIN_EMAIL is required"
        exec python -m app.commands.reset_qa_admin
        ;;
    migrate_admin_email)
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${APP_ENV:-}" = "test" ] || fail "QA application commands must use APP_ENV=test"
        [ -n "${QA_ADMIN_EMAIL:-}" ] || fail "QA_ADMIN_EMAIL is required"
        [ "$#" -eq 4 ] && [ "$1" = "--old-email" ] && [ "$3" = "--new-email" ] || fail "Provide --old-email and --new-email"
        [ "$4" = "$QA_ADMIN_EMAIL" ] || fail "New email must match QA_ADMIN_EMAIL"
        exec python -m app.commands.migrate_qa_admin_email "$@"
        ;;
    api_test)
        [ "${MYSQL_TEST_DATABASE_KIND:-}" = "disposable" ] || fail "MySQL test database must be marked disposable"
        [ "${MYSQL_TEST_DATABASE_URL:-}" = "$expected_url" ] || fail "MySQL test and Alembic URLs must match"
        [ "${DB_HOST:-}" = "qa-mysql" ] || fail "API tests must connect to qa-mysql"
        [ "${DB_PORT:-}" = "3306" ] || fail "API tests must use the QA MySQL port"
        [ "${DB_NAME:-}" = "nexus_qa_test" ] || fail "API tests must use nexus_qa_test"
        [ "${DB_USER:-}" = "nexus_qa_runner" ] || fail "API tests must use the QA database user"
        [ "${DB_PASSWORD:-}" = "$password" ] || fail "API test credentials must match the QA database credentials"
        [ "${BASE_URL:-}" = "http://api:8000" ] || fail "API tests must target the QA API"
        [ -n "${TEST_USER_EMAIL:-}" ] || fail "QA API test user email is required"
        [ "${TEST_USER_EMAIL:-}" = "${QA_ADMIN_EMAIL:-}" ] || fail "API tests must use the QA administrator account"
        require_hex_secret TEST_USER_PASSWORD "${TEST_USER_PASSWORD:-}" 64
        require_hex_secret TEST_RBAC_PASSWORD "${TEST_RBAC_PASSWORD:-}" 64
        [ "$TEST_USER_PASSWORD" != "$TEST_RBAC_PASSWORD" ] || fail "QA administrator and RBAC test passwords must differ"
        exec pytest -q
        ;;
    qa_admission_migrate)
        [ "$#" -eq 0 ] || fail "QA admission migration does not accept command or selector overrides"
        validate_admission_test_project
        [ "${APP_ENV:-}" = "test" ] || fail "QA admission migration must use APP_ENV=test"
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        exec alembic upgrade head
        ;;
    qa_admission_mysql_test)
        [ "$#" -eq 0 ] || fail "QA admission tests do not accept command or selector overrides"
        validate_admission_test_project
        [ "${APP_ENV:-}" = "test" ] || fail "QA admission integration tests must use APP_ENV=test"
        require_hex_secret JWT_SECRET_KEY "${JWT_SECRET_KEY:-}" 64
        [ "${MYSQL_TEST_DATABASE_KIND:-}" = "disposable" ] || fail "MySQL test database must be marked disposable"
        [ "${MYSQL_TEST_DATABASE_URL:-}" = "$expected_url" ] || fail "QA admission tests must target the isolated QA database"
        exec python -m pytest tests/test_qa_execution_admissions_mysql.py -v -p no:cacheprovider
        ;;
esac

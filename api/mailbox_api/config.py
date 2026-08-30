from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "sqlite:///./mailbox-api.db"
    api_base_url: str = "https://mail.i7wap.xyz"
    default_domain: str = "i7wap.xyz"
    mail_public_ip: str = "103.214.172.30"
    admin_api_key: str = "dev-admin-change-me"
    app_secret: str = "dev-secret-change-me"
    token_bytes: int = 24
    mailbox_secret_bytes: int = 18
    mailbox_quota_bytes: int = 0
    mailu_sync_enabled: bool = False
    mailu_admin_api_url: str = "http://mailu-admin:80/api/v1"
    mailu_admin_auth_url: str = "http://admin:8080/internal/auth/admin"
    mailu_api_token: str | None = None
    mailu_timeout_seconds: float = 10.0
    mailu_imap_host: str = "front"
    mailu_imap_port: int = 143
    mailu_imap_ssl: bool = False
    mailu_imap_timeout_seconds: float = 20.0
    worker_poll_seconds: float = 10.0
    worker_concurrency: int = 8
    worker_batch_size: int = 32
    worker_fetch_limit: int = 10
    mail_read_refresh_enabled: bool = True
    db_pool_size: int = 20
    db_max_overflow: int = 80
    box_require_mailu_admin_session: bool = True
    cloudflare_sync_enabled: bool = False
    cloudflare_api_token: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_timeout_seconds: float = 20.0


settings = Settings()

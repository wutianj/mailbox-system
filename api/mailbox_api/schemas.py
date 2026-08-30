from datetime import datetime
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DomainCreate(BaseModel):
    domain: str
    mx_host: str
    api_base_url: str
    status: str = "active"
    sync_with_mail_backend: bool = True


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    mx_host: str
    api_base_url: str
    status: str


class BatchMailboxCreate(BaseModel):
    domain: str
    count: int = Field(ge=1, le=100000)
    mx_host: str | None = None
    api_base_url: str | None = None
    prefix: str = ""
    label: str | None = None
    group_name: str | None = None
    quota_bytes: int | None = Field(default=None, ge=0)
    enable_imap: bool = True
    enable_pop: bool = True
    spam_enabled: bool = True
    sync_with_mail_backend: bool = True
    auto_setup_domain: bool = True
    sync_cloudflare: bool = True


class MailboxCreated(BaseModel):
    email: EmailStr
    token: str
    api_url: str
    password: str


class BatchMailboxCreateResponse(BaseModel):
    count: int
    export: str
    items: list[MailboxCreated]


class DnsRecordPlan(BaseModel):
    type: str
    name: str
    content: str | None = None
    priority: int | None = None
    proxied: bool | None = None
    status: str = "pending"
    message: str | None = None


class DomainPrepareRequest(BaseModel):
    domain: str
    mx_host: str | None = None
    api_base_url: str | None = None
    sync_with_mail_backend: bool = True
    sync_cloudflare: bool = True


class DomainPrepareResponse(BaseModel):
    domain: str
    mx_host: str
    api_base_url: str
    domain_created: bool
    domain_updated: bool
    mailu_synced: bool
    dkim_generated: bool
    cloudflare_enabled: bool
    cloudflare_zone_status: str
    nameservers: list[str] = []
    records: list[DnsRecordPlan]
    ready: bool
    message: str


class MailboxExportRequest(BaseModel):
    domain: str = "i7wap.xyz"


class MailboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    status: str
    mail_backend_status: str
    label: str | None
    group_name: str | None
    created_at: datetime


class RotateTokenResponse(BaseModel):
    email: EmailStr
    token: str
    api_url: str


class MessageCreate(BaseModel):
    email: EmailStr
    from_addr: str | None = None
    subject: str | None = None
    text_body: str | None = None
    html_body: str | None = None
    message_id: str | None = None
    raw_path: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_addr: str | None
    to_addr: str
    subject: str | None
    text_body: str | None
    html_body: str | None
    code: str | None
    verified_link: str | None
    received_at: datetime


class MailCodeResponse(BaseModel):
    ok: bool
    email: EmailStr
    found: bool
    code: str | None = None
    link: str | None = None
    subject: str | None = None
    received_at: datetime | None = None


def build_api_url(api_base_url: str, token: str, email: str) -> str:
    return f"{api_base_url.rstrip('/')}/mail/{token}/{quote(email, safe='')}"

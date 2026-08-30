from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
import logging
import re
import secrets
import string
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
import httpx
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cloudflare import CloudflareClientError, configured_cloudflare_client
from .config import settings
from .db import Base, engine, get_session
from .extractors import extract_code, extract_link, visible_text
from .mailu import MailuClientError, configured_mailu_client
from .models import Domain, Mailbox, Message
from .schemas import (
    BatchMailboxCreate,
    BatchMailboxCreateResponse,
    DnsRecordPlan,
    DomainCreate,
    DomainOut,
    DomainPrepareRequest,
    DomainPrepareResponse,
    MailCodeResponse,
    MailboxExportRequest,
    MailboxCreated,
    MailboxOut,
    MessageCreate,
    MessageOut,
    RotateTokenResponse,
    build_api_url,
)
from .security import decrypt_value, encrypt_value, generate_secret, generate_token, hash_value, verify_value
from .worker import poll_mailbox


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Mailbox API", version="0.1.0", lifespan=lifespan)
log = logging.getLogger("mailbox-api")

@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True, "mail_backend_enabled": settings.mailu_sync_enabled}


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not x_admin_token or not verify_value(x_admin_token, hash_value(settings.admin_api_key)):
        raise HTTPException(status_code=403, detail="admin_auth_required")


def require_box_admin(request: Request) -> None:
    if not _mailu_admin_session_ok(request):
        raise HTTPException(status_code=403, detail="admin_login_required")


def _mailu_admin_session_ok(request: Request) -> bool:
    if not settings.box_require_mailu_admin_session:
        return True
    cookie = request.headers.get("cookie")
    if not cookie:
        return False
    headers = {
        "Cookie": cookie,
        "X-Forwarded-Proto": request.headers.get("x-forwarded-proto", "https"),
    }
    if request.client and request.client.host:
        headers["X-Real-IP"] = request.client.host
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False) as client:
            response = client.get(settings.mailu_admin_auth_url, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="mailu_admin_auth_unavailable") from exc
    return response.status_code == 200


@app.post("/admin/domains", response_model=DomainOut)
def create_domain(
    payload: DomainCreate,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Domain:
    existing = session.scalar(select(Domain).where(Domain.domain == payload.domain.lower()))
    if existing:
        raise HTTPException(status_code=409, detail="domain_exists")
    if payload.sync_with_mail_backend:
        _sync_domain_with_mail_backend(payload.domain.lower())
    domain = Domain(
        domain=payload.domain.lower(),
        mx_host=payload.mx_host.lower(),
        api_base_url=payload.api_base_url.rstrip("/"),
        status=payload.status,
    )
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


@app.get("/admin/domains", response_model=list[DomainOut])
def list_domains(_: None = Depends(require_admin), session: Session = Depends(get_session)) -> list[Domain]:
    return list(session.scalars(select(Domain).order_by(Domain.domain)).all())


@app.post("/admin/mailboxes/batch", response_model=BatchMailboxCreateResponse)
def create_mailboxes(
    payload: BatchMailboxCreate,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> BatchMailboxCreateResponse:
    return _create_mailbox_batch(payload, session)


@app.get("/box", response_class=HTMLResponse)
def mailbox_batch_page(request: Request) -> Response:
    if not _mailu_admin_session_ok(request):
        return RedirectResponse(url="/sso/login?url=" + quote("/box", safe=""), status_code=302)
    return HTMLResponse(_mailbox_batch_html())


@app.post("/box/domain/prepare", response_model=DomainPrepareResponse)
def browser_prepare_domain(
    payload: DomainPrepareRequest,
    _: None = Depends(require_box_admin),
    session: Session = Depends(get_session),
) -> DomainPrepareResponse:
    return _prepare_domain_for_box(payload, session)


@app.post("/box/download")
def browser_download_generated_mailboxes(
    payload: BatchMailboxCreate,
    _: None = Depends(require_box_admin),
    session: Session = Depends(get_session),
) -> Response:
    if payload.auto_setup_domain:
        _prepare_domain_for_box(_domain_prepare_request_from_batch(payload), session)
    result = _create_mailbox_batch(payload, session)
    filename = _export_filename(payload.domain, result.count, "generated")
    return Response(
        content=result.export + ("\n" if result.export else ""),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Mailbox-Count": str(result.count),
        },
    )


@app.post("/box/generate", response_model=BatchMailboxCreateResponse)
def browser_generate_mailboxes(
    payload: BatchMailboxCreate,
    _: None = Depends(require_box_admin),
    session: Session = Depends(get_session),
) -> BatchMailboxCreateResponse:
    if payload.auto_setup_domain:
        _prepare_domain_for_box(_domain_prepare_request_from_batch(payload), session)
    return _create_mailbox_batch(payload, session)


@app.post("/box/export")
def browser_download_active_mailboxes(
    payload: MailboxExportRequest,
    _: None = Depends(require_box_admin),
    session: Session = Depends(get_session),
) -> Response:
    export = _active_mailbox_export(session, payload.domain)
    count = len([line for line in export.splitlines() if line.strip()])
    filename = _export_filename(payload.domain, count, "active")
    return Response(
        content=export + ("\n" if export else ""),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Mailbox-Count": str(count),
        },
    )


def _create_mailbox_batch(payload: BatchMailboxCreate, session: Session) -> BatchMailboxCreateResponse:
    normalized_domain = _normalize_domain(payload.domain)
    domain = session.scalar(select(Domain).where(Domain.domain == normalized_domain))
    if not domain:
        raise HTTPException(status_code=404, detail="domain_not_found")
    if domain.status != "active":
        raise HTTPException(status_code=409, detail="domain_not_active")

    items: list[MailboxCreated] = []
    reserved_local_parts: set[str] = set()
    for _ in range(payload.count):
        local_part, email, token, password = _generate_mailbox_credentials(
            session, domain, payload.prefix, reserved_local_parts
        )
        reserved_local_parts.add(local_part)
        mail_backend_status = "local-only"
        mail_backend_error = None
        if payload.sync_with_mail_backend:
            try:
                _sync_mailbox_with_mail_backend(
                    email=email,
                    password=password,
                    quota_bytes=payload.quota_bytes if payload.quota_bytes is not None else settings.mailbox_quota_bytes,
                    enable_imap=payload.enable_imap,
                    enable_pop=payload.enable_pop,
                    spam_enabled=payload.spam_enabled,
                )
                mail_backend_status = "synced"
            except MailuClientError as exc:
                mail_backend_status = "failed"
                mail_backend_error = str(exc)
                raise HTTPException(status_code=502, detail="mail_backend_create_failed") from exc
        mailbox = Mailbox(
            domain_id=domain.id,
            local_part=local_part,
            email=email,
            password_hash=hash_value(password),
            password_ciphertext=encrypt_value(password),
            token_hash=hash_value(token),
            token_ciphertext=encrypt_value(token),
            mail_backend_status=mail_backend_status,
            mail_backend_error=mail_backend_error,
            label=payload.label,
            group_name=payload.group_name,
        )
        session.add(mailbox)
        items.append(
            MailboxCreated(
                email=email,
                token=token,
                password=password,
                api_url=build_api_url(domain.api_base_url, token, email),
            )
        )
    session.commit()
    export = "\n".join(f"{item.email}----{item.api_url}" for item in items)
    return BatchMailboxCreateResponse(count=len(items), export=export, items=items)


def _domain_prepare_request_from_batch(payload: BatchMailboxCreate) -> DomainPrepareRequest:
    return DomainPrepareRequest(
        domain=payload.domain,
        mx_host=payload.mx_host,
        api_base_url=payload.api_base_url,
        sync_with_mail_backend=payload.sync_with_mail_backend,
        sync_cloudflare=payload.sync_cloudflare,
    )


def _prepare_domain_for_box(payload: DomainPrepareRequest, session: Session) -> DomainPrepareResponse:
    domain = _normalize_domain(payload.domain)
    mx_host = _normalize_hostname(payload.mx_host) if payload.mx_host else f"mail.{domain}"
    api_base_url = _normalize_api_base_url(payload.api_base_url or settings.api_base_url)

    mailu_dns: dict[str, str] = {}
    mailu_synced = False
    dkim_generated = False
    if payload.sync_with_mail_backend:
        mailu_dns, dkim_generated = _ensure_mailu_domain_ready(domain)
        mailu_synced = True

    domain_created, domain_updated = _upsert_domain_row(session, domain, mx_host, api_base_url)
    records = _planned_dns_records(domain, mx_host, mailu_dns)
    cloudflare_enabled = bool(payload.sync_cloudflare and settings.cloudflare_sync_enabled)
    cloudflare_zone_status = "skipped" if not payload.sync_cloudflare else "disabled"
    nameservers: list[str] = []

    if payload.sync_cloudflare:
        try:
            client = configured_cloudflare_client(
                enabled=settings.cloudflare_sync_enabled,
                api_token=settings.cloudflare_api_token,
                account_id=settings.cloudflare_account_id,
                timeout_seconds=settings.cloudflare_timeout_seconds,
            )
            if client:
                zone = client.find_best_zone(domain)
                if not zone:
                    zone = client.create_zone(domain)
                    cloudflare_zone_status = "created"
                else:
                    cloudflare_zone_status = str(zone.get("status") or "found")
                nameservers = [str(item) for item in zone.get("name_servers", []) if item]
                zone_id = str(zone["id"])
                zone_name = str(zone.get("name") or domain)
                _sync_records_to_cloudflare(client, zone_id, zone_name, records)
        except CloudflareClientError as exc:
            cloudflare_zone_status = "failed"
            for record in records:
                if record.status == "pending":
                    record.status = "failed"
                    record.message = str(exc)

    ready = _domain_prepare_ready(payload, mailu_synced, cloudflare_enabled, cloudflare_zone_status, records)
    message = _domain_prepare_message(ready, cloudflare_zone_status, nameservers)
    return DomainPrepareResponse(
        domain=domain,
        mx_host=mx_host,
        api_base_url=api_base_url,
        domain_created=domain_created,
        domain_updated=domain_updated,
        mailu_synced=mailu_synced,
        dkim_generated=dkim_generated,
        cloudflare_enabled=cloudflare_enabled,
        cloudflare_zone_status=cloudflare_zone_status,
        nameservers=nameservers,
        records=records,
        ready=ready,
        message=message,
    )


def _ensure_mailu_domain_ready(domain: str) -> tuple[dict[str, str], bool]:
    client = configured_mailu_client(
        enabled=settings.mailu_sync_enabled,
        api_url=settings.mailu_admin_api_url,
        api_token=settings.mailu_api_token,
        timeout_seconds=settings.mailu_timeout_seconds,
    )
    if not client:
        return {}, False
    try:
        client.ensure_domain(domain)
        details = client.get_domain(domain)
        dkim_generated = False
        if not details.get("dns_dkim"):
            client.generate_dkim(domain)
            dkim_generated = True
            details = client.get_domain(domain)
        return {key: str(value) for key, value in details.items() if isinstance(value, str)}, dkim_generated
    except MailuClientError as exc:
        raise HTTPException(status_code=502, detail="mail_backend_domain_failed") from exc


def _upsert_domain_row(session: Session, domain: str, mx_host: str, api_base_url: str) -> tuple[bool, bool]:
    created = False
    updated = False
    domain_obj = session.scalar(select(Domain).where(Domain.domain == domain))
    if not domain_obj:
        domain_obj = Domain(domain=domain, mx_host=mx_host, api_base_url=api_base_url, status="active")
        session.add(domain_obj)
        try:
            session.commit()
            return True, False
        except IntegrityError:
            session.rollback()
            domain_obj = session.scalar(select(Domain).where(Domain.domain == domain))
            created = False
    if not domain_obj:
        raise HTTPException(status_code=500, detail="domain_upsert_failed")
    if domain_obj.mx_host != mx_host:
        domain_obj.mx_host = mx_host
        updated = True
    if domain_obj.api_base_url != api_base_url:
        domain_obj.api_base_url = api_base_url
        updated = True
    if domain_obj.status != "active":
        domain_obj.status = "active"
        updated = True
    if updated:
        session.add(domain_obj)
        session.commit()
    return created, updated


def _planned_dns_records(domain: str, mx_host: str, mailu_dns: dict[str, str]) -> list[DnsRecordPlan]:
    records: list[DnsRecordPlan] = []
    if _hostname_in_domain(mx_host, domain):
        records.append(
            DnsRecordPlan(type="A", name=mx_host, content=settings.mail_public_ip, proxied=False, message="mail_host")
        )
    records.extend(
        [
            DnsRecordPlan(type="MX", name=domain, content=mx_host, priority=10, message="mail_route"),
            DnsRecordPlan(
                type="TXT",
                name=domain,
                content=_txt_value_from_bind_record(mailu_dns.get("dns_spf")) or f"v=spf1 mx ip4:{settings.mail_public_ip} ~all",
                message="spf",
            ),
        ]
    )
    dkim_name = _record_name_from_bind_record(mailu_dns.get("dns_dkim"))
    dkim_value = _txt_value_from_bind_record(mailu_dns.get("dns_dkim"))
    if dkim_name and dkim_value:
        records.append(DnsRecordPlan(type="TXT", name=dkim_name, content=dkim_value, message="dkim"))
    else:
        records.append(DnsRecordPlan(type="TXT", name=f"dkim._domainkey.{domain}", status="skipped", message="dkim_missing"))
    records.append(
        DnsRecordPlan(
            type="TXT",
            name=f"_dmarc.{domain}",
            content=_txt_value_from_bind_record(mailu_dns.get("dns_dmarc")) or "v=DMARC1; p=none; adkim=s; aspf=s",
            message="dmarc",
        )
    )
    return records


def _sync_records_to_cloudflare(client, zone_id: str, zone_name: str, records: list[DnsRecordPlan]) -> None:
    for record in records:
        if not record.content:
            record.status = "skipped"
            continue
        if not _hostname_in_domain(record.name, zone_name):
            record.status = "skipped"
            record.message = "not_in_cloudflare_zone"
            continue
        try:
            record.status = client.upsert_dns_record(
                zone_id=zone_id,
                record_type=record.type,
                name=record.name,
                content=record.content,
                priority=record.priority,
                proxied=record.proxied,
                match_content_prefix=_dns_match_prefix(record),
            )
        except CloudflareClientError as exc:
            record.status = "failed"
            record.message = str(exc)


def _dns_match_prefix(record: DnsRecordPlan) -> str | None:
    if record.type.upper() == "TXT" and record.content:
        if record.content.startswith("v=spf1"):
            return "v=spf1"
        if record.content.startswith("v=DMARC1"):
            return "v=DMARC1"
        if record.content.startswith("v=DKIM1"):
            return "v=DKIM1"
    return None


def _domain_prepare_ready(
    payload: DomainPrepareRequest,
    mailu_synced: bool,
    cloudflare_enabled: bool,
    cloudflare_zone_status: str,
    records: list[DnsRecordPlan],
) -> bool:
    if payload.sync_with_mail_backend and not mailu_synced:
        return False
    if cloudflare_enabled and cloudflare_zone_status in {"failed", "disabled"}:
        return False
    return not any(record.status == "failed" for record in records)


def _domain_prepare_message(ready: bool, cloudflare_zone_status: str, nameservers: list[str]) -> str:
    if not ready:
        return "域名自动部署失败，先处理页面里标红的 DNS/Cloudflare 状态"
    if nameservers and cloudflare_zone_status in {"created", "pending"}:
        return "Cloudflare 已准备好，请在域名服务商把 NS 改成页面显示的 nameserver"
    if nameservers:
        return "域名自动部署完成，确认域名服务商 NS 已指向页面显示的 Cloudflare nameserver"
    return "域名已在邮箱后台准备好"


def _record_name_from_bind_record(record: str | None) -> str | None:
    if not record:
        return None
    first = record.strip().split(maxsplit=1)[0] if record.strip() else ""
    return first.rstrip(".") or None


def _txt_value_from_bind_record(record: str | None) -> str | None:
    if not record:
        return None
    parts = re.findall(r'"([^"]*)"', record)
    return "".join(parts) if parts else None


def _normalize_domain(raw_domain: str) -> str:
    domain = raw_domain.strip().lower().rstrip(".")
    if not domain or any(ch in domain for ch in " /:@"):
        raise HTTPException(status_code=422, detail="invalid_domain")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(status_code=422, detail="invalid_domain") from exc
    labels = domain.split(".")
    if len(labels) < 2 or len(domain) > 253:
        raise HTTPException(status_code=422, detail="invalid_domain")
    for label in labels:
        if not label or len(label) > 63 or label.startswith("-") or label.endswith("-"):
            raise HTTPException(status_code=422, detail="invalid_domain")
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise HTTPException(status_code=422, detail="invalid_domain")
    return domain


def _normalize_hostname(raw_host: str | None) -> str:
    return _normalize_domain(raw_host or "")


def _normalize_api_base_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="invalid_api_base_url")
    return value


def _hostname_in_domain(hostname: str, domain: str) -> bool:
    hostname = hostname.rstrip(".").lower()
    domain = domain.rstrip(".").lower()
    return hostname == domain or hostname.endswith(f".{domain}")


@app.get("/admin/export", response_class=PlainTextResponse)
def export_mailboxes(
    domain: str,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> str:
    return _active_mailbox_export(session, domain)


def _active_mailbox_export(session: Session, domain: str) -> str:
    domain_obj = session.scalar(select(Domain).where(Domain.domain == domain.lower()))
    if not domain_obj:
        raise HTTPException(status_code=404, detail="domain_not_found")
    mailboxes = session.scalars(
        select(Mailbox).where(Mailbox.domain_id == domain_obj.id).order_by(Mailbox.id)
    ).all()
    lines = [
        f"{m.email}----{build_api_url(domain_obj.api_base_url, decrypt_value(m.token_ciphertext), m.email)}"
        for m in mailboxes
        if m.status == "active"
    ]
    return "\n".join(lines)


def _export_filename(domain: str, count: int, kind: str) -> str:
    safe_domain = "".join(ch for ch in domain.lower() if ch.isalnum() or ch in {".", "-"}).strip(".-") or "domain"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{safe_domain}-{kind}-{count}.txt"


def _mailbox_batch_html() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mailbox System - 批量生成</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef3f6;
      --panel: #ffffff;
      --ink: #1f2933;
      --muted: #637381;
      --line: #d6e0e7;
      --brand: #2f86bf;
      --brand-dark: #236b99;
      --ok: #287d3c;
      --warn: #9b5c00;
      --bad: #b42318;
      --field: #f8fafc;
      --shadow: 0 1px 2px rgba(15, 23, 42, .12);
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-size: 14px;
    }
    .layout { min-height: 100vh; display: grid; grid-template-columns: 246px 1fr; }
    .side { background: #343c42; color: #dbe6ec; }
    .brand {
      height: 56px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 16px;
      background: var(--brand);
      color: #fff;
      font-size: 18px;
    }
    .brand-mark {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      background: rgba(255, 255, 255, .2);
      font-weight: 800;
    }
    .nav { padding: 18px 14px; }
    .account { padding: 12px 0 18px; border-bottom: 1px solid rgba(255,255,255,.12); }
    .nav-title { margin: 22px 0 10px; color: #65b7ea; font-size: 14px; }
    .nav-item {
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 34px;
      padding: 0 4px;
      color: #d7e0e5;
    }
    .nav-item.active { color: #ffffff; font-weight: 700; }
    .main { min-width: 0; }
    .topbar {
      height: 56px;
      display: flex;
      align-items: center;
      padding: 0 20px;
      background: #fff;
      border-bottom: 1px solid var(--line);
      color: #65727d;
    }
    .content { padding: 14px 10px 32px; }
    h1 { margin: 0 0 4px; font-size: 24px; line-height: 1.25; font-weight: 800; }
    .subtitle { margin: 0 0 16px; color: var(--muted); font-size: 13px; }
    .panel {
      background: var(--panel);
      border-top: 3px solid #54ace4;
      border-left: 1px solid var(--line);
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      box-shadow: var(--shadow);
      margin-bottom: 16px;
    }
    .panel h2 {
      margin: 0;
      padding: 12px 18px;
      font-size: 16px;
      border-bottom: 1px solid var(--line);
    }
    .form-grid { display: grid; grid-template-columns: 250px 1fr; }
    .row { display: contents; }
    label, .field-cell {
      min-height: 48px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    label {
      display: flex;
      align-items: center;
      font-weight: 700;
      border-right: 1px solid var(--line);
      background: #fbfdfe;
    }
    .field-cell { display: flex; gap: 10px; align-items: center; }
    .field-stack {
      width: 100%;
      display: grid;
      gap: 8px;
    }
    .domain-status {
      display: grid;
      gap: 6px;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: #f7fafc;
      color: var(--muted);
      line-height: 1.6;
      font-size: 13px;
    }
    .domain-status.ok { color: var(--ok); border-color: #9bd0a8; background: #f2fbf4; }
    .domain-status.warn { color: var(--warn); border-color: #e1bd7b; background: #fffaf0; }
    .domain-status.bad { color: var(--bad); border-color: #f2aaa3; background: #fff5f4; }
    .domain-status strong { color: var(--ink); }
    .domain-status code { word-break: break-all; }
    input, select {
      width: 100%;
      height: 38px;
      border: 1px solid #cfd9e2;
      border-radius: 4px;
      background: var(--field);
      padding: 0 10px;
      font: inherit;
      color: var(--ink);
    }
    input:focus, select:focus, button:focus {
      outline: 2px solid rgba(47, 134, 191, .25);
      border-color: var(--brand);
    }
    .inline { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; width: 100%; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 14px 18px 18px;
      align-items: center;
    }
    button {
      height: 38px;
      border: 1px solid var(--brand-dark);
      border-radius: 4px;
      background: var(--brand);
      color: #fff;
      padding: 0 14px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      background: #f6fafc;
      color: var(--brand-dark);
      border-color: #9dc8e3;
    }
    button:disabled { opacity: .6; cursor: wait; }
    .status {
      min-height: 38px;
      display: flex;
      align-items: center;
      padding: 0 12px;
      border-radius: 4px;
      background: #f7fafc;
      color: var(--muted);
      border: 1px solid var(--line);
    }
    .status.ok { color: var(--ok); border-color: #9bd0a8; background: #f2fbf4; }
    .status.warn { color: var(--warn); border-color: #e1bd7b; background: #fffaf0; }
    .status.bad { color: var(--bad); border-color: #f2aaa3; background: #fff5f4; }
    .result-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
    }
    .result-head h2 { padding: 0; border: 0; }
    .result-count { color: var(--muted); }
    .result-box { padding: 14px 18px 18px; }
    textarea {
      width: 100%;
      min-height: 360px;
      resize: vertical;
      border: 1px solid #cfd9e2;
      border-radius: 4px;
      background: #fbfdff;
      padding: 12px;
      color: #17212b;
      font: 13px/1.6 "Consolas", "Microsoft YaHei", monospace;
      white-space: pre;
    }
    textarea:focus {
      outline: 2px solid rgba(47, 134, 191, .25);
      border-color: var(--brand);
    }
    .help {
      margin: 0;
      padding: 14px 18px 18px;
      color: var(--muted);
      line-height: 1.75;
      border-top: 1px solid var(--line);
    }
    code {
      background: #edf4f8;
      border: 1px solid #d4e4ee;
      border-radius: 3px;
      padding: 1px 5px;
      color: #235a7f;
    }
    @media (max-width: 820px) {
      .layout { grid-template-columns: 1fr; }
      .side { display: none; }
      .content { padding: 12px; }
      .form-grid { grid-template-columns: 1fr; }
      label { border-right: 0; min-height: auto; padding-bottom: 4px; }
      .field-cell { padding-top: 4px; }
      .inline { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="side">
      <div class="brand"><span class="brand-mark">M</span><span>Mailbox System</span></div>
      <div class="nav">
        <div class="account">postmaster@i7wap.xyz</div>
        <div class="nav-title">管理</div>
        <div class="nav-item active">批量生成</div>
        <div class="nav-item">邮箱域</div>
        <div class="nav-item">接码接口</div>
        <div class="nav-title">转到</div>
        <div class="nav-item">网页邮箱</div>
        <div class="nav-item">网站</div>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">☰</div>
      <div class="content">
        <h1>批量生成邮箱</h1>
        <p class="subtitle">生成真实邮箱账号，结果先显示在网页上，再按需复制或下载。</p>

        <section class="panel">
          <h2>生成到网页</h2>
          <form id="generate-form" class="form-grid">
            <div class="row">
              <label for="domain">邮箱域名</label>
              <div class="field-cell">
                <div class="field-stack">
                  <input id="domain" name="domain" value="i7wap.xyz">
                  <div id="domain-status" class="domain-status">
                    <div>填写域名后会先自动同步 Mailu 和 Cloudflare；域名服务商只需要按这里返回的 NS 托管域名。</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="row">
              <label for="count">数量 / 每批数量</label>
              <div class="field-cell">
                <div class="inline">
                  <input id="count" name="count" type="number" min="1" max="100000" value="1000">
                  <input id="chunk-size" name="chunk-size" type="number" min="1" max="1000" value="1000">
                </div>
              </div>
            </div>
            <div class="row">
              <label for="group">批次 / 标签</label>
              <div class="field-cell">
                <div class="inline">
                  <input id="group" name="group" placeholder="batch-001">
                  <input id="label" name="label" placeholder="运营备注，可空">
                </div>
              </div>
            </div>
            <div class="row">
              <label for="api-base-url">接码 API 域名</label>
              <div class="field-cell">
                <input id="api-base-url" name="api-base-url" value="https://mail.i7wap.xyz">
              </div>
            </div>
          </form>
          <div class="actions">
            <button id="generate-button" type="button">开始生成</button>
            <button id="prepare-domain-button" class="secondary" type="button">同步域名</button>
            <button id="export-button" class="secondary" type="button">载入现有有效邮箱</button>
            <span id="status" class="status">等待操作</span>
          </div>
          <p class="help">
            新邮箱默认使用无前缀随机本地名。输出格式为 <code>email----api_url</code>，每条 API 只能读取对应邮箱的最新验证码或链接。
          </p>
        </section>
        <section class="panel">
          <div class="result-head">
            <h2>生成结果</h2>
            <span id="result-count" class="result-count">0 条</span>
          </div>
          <div class="actions">
            <button id="copy-button" class="secondary" type="button" disabled>复制全部</button>
            <button id="download-button" class="secondary" type="button" disabled>下载 TXT</button>
            <button id="clear-button" class="secondary" type="button">清空结果</button>
          </div>
          <div class="result-box">
            <textarea id="results" readonly placeholder="生成后的 email----api_url 会显示在这里"></textarea>
          </div>
        </section>
      </div>
    </main>
  </div>
  <script>
    const statusEl = document.getElementById("status");
    const domainStatusEl = document.getElementById("domain-status");
    const generateButton = document.getElementById("generate-button");
    const prepareDomainButton = document.getElementById("prepare-domain-button");
    const exportButton = document.getElementById("export-button");
    const copyButton = document.getElementById("copy-button");
    const downloadButton = document.getElementById("download-button");
    const clearButton = document.getElementById("clear-button");
    const resultsEl = document.getElementById("results");
    const resultCountEl = document.getElementById("result-count");
    let resultLines = [];

    function setStatus(text, mode) {
      statusEl.textContent = text;
      statusEl.className = "status" + (mode ? " " + mode : "");
    }

    function setDomainStatus(html, mode) {
      domainStatusEl.innerHTML = html;
      domainStatusEl.className = "domain-status" + (mode ? " " + mode : "");
    }

    function setBusy(busy) {
      generateButton.disabled = busy;
      prepareDomainButton.disabled = busy;
      exportButton.disabled = busy;
      copyButton.disabled = busy || resultLines.length === 0;
      downloadButton.disabled = busy || resultLines.length === 0;
      clearButton.disabled = busy;
    }

    function updateResults(lines, append) {
      resultLines = append ? resultLines.concat(lines) : lines;
      resultsEl.value = resultLines.join("\n");
      resultCountEl.textContent = resultLines.length + " 条";
      copyButton.disabled = resultLines.length === 0;
      downloadButton.disabled = resultLines.length === 0;
    }

    function basePayload(count) {
      const domain = document.getElementById("domain").value.trim();
      return {
        domain,
        mx_host: "mail." + domain,
        api_base_url: document.getElementById("api-base-url").value.trim(),
        count,
        prefix: "",
        group_name: document.getElementById("group").value.trim() || null,
        label: document.getElementById("label").value.trim() || null,
        sync_with_mail_backend: true,
        auto_setup_domain: true,
        sync_cloudflare: true
      };
    }

    function currentFilename(kind) {
      const domain = (document.getElementById("domain").value.trim() || "i7wap.xyz").replace(/[^a-z0-9.-]/gi, "-");
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
      return stamp + "-" + domain + "-" + kind + "-" + resultLines.length + ".txt";
    }

    function downloadCurrent() {
      const blob = new Blob([resultLines.join("\n") + (resultLines.length ? "\n" : "")], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = currentFilename("generated");
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus("已下载当前结果：" + resultLines.length + " 条", "ok");
    }

    async function postJson(endpoint, body) {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        credentials: "same-origin",
        body: JSON.stringify(body)
      });
      if (!response.ok) {
        const text = await response.text();
        if (response.status === 403) {
          throw new Error("请先登录管理后台，再打开 /box");
        }
        throw new Error(text || ("HTTP " + response.status));
      }
      return response;
    }

    function escapeHtml(value) {
      return String(value == null ? "" : value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function renderDomainPrepare(body) {
      const mode = body.ready ? "ok" : "bad";
      const ns = (body.nameservers || []).map(item => "<code>" + escapeHtml(item) + "</code>").join(" / ");
      const rows = (body.records || []).map(record => {
        const bits = [record.type, record.name, record.status].map(escapeHtml).join(" · ");
        return "<div><code>" + bits + "</code></div>";
      }).join("");
      setDomainStatus(
        "<div><strong>" + escapeHtml(body.domain) + "</strong> · " + escapeHtml(body.message) + "</div>" +
        (ns ? "<div>域名服务商 NS：" + ns + "</div>" : "") +
        "<div>MX：" + escapeHtml(body.mx_host) + "；API：" + escapeHtml(body.api_base_url) + "</div>" +
        (rows ? "<div>" + rows + "</div>" : ""),
        mode
      );
    }

    async function prepareDomain() {
      const domain = document.getElementById("domain").value.trim();
      if (!domain) {
        throw new Error("请先填写邮箱域名");
      }
      setDomainStatus("<div>正在同步域名、DKIM 和 Cloudflare DNS...</div>", "warn");
      const response = await postJson("/box/domain/prepare", {
        domain,
        mx_host: "mail." + domain,
        api_base_url: document.getElementById("api-base-url").value.trim(),
        sync_with_mail_backend: true,
        sync_cloudflare: true
      });
      const body = await response.json();
      renderDomainPrepare(body);
      if (!body.ready) {
        throw new Error(body.message || "域名还没有准备好");
      }
      return body;
    }

    async function generateToPage() {
      const total = Number(document.getElementById("count").value);
      const rawChunkSize = Number(document.getElementById("chunk-size").value);
      if (!Number.isInteger(total) || total < 1) {
        setStatus("数量必须大于 0", "bad");
        return;
      }
      if (!Number.isInteger(rawChunkSize) || rawChunkSize < 1) {
        setStatus("每批数量必须大于 0", "bad");
        return;
      }
      const chunkSize = Math.min(rawChunkSize, 1000, total);
      setBusy(true);
      updateResults([], false);
      try {
        setStatus("正在准备域名", "warn");
        await prepareDomain();
        let done = 0;
        while (done < total) {
          const count = Math.min(chunkSize, total - done);
          setStatus("正在生成 " + (done + 1) + "-" + (done + count) + " / " + total, "warn");
          const response = await postJson("/box/generate", basePayload(count));
          const body = await response.json();
          const lines = (body.export || "").split(/\r?\n/).filter(Boolean);
          updateResults(lines, true);
          done += lines.length;
          if (lines.length !== count) {
            throw new Error("返回数量不一致，已停止");
          }
        }
        setStatus("生成完成：" + resultLines.length + " 条，可复制或下载", "ok");
      } catch (err) {
        setStatus("失败：" + err.message + "；已保留当前页面结果", "bad");
      } finally {
        setBusy(false);
      }
    }

    async function loadActiveMailboxes() {
      setBusy(true);
      setStatus("正在载入现有有效邮箱", "warn");
      try {
        const response = await postJson("/box/export", { domain: document.getElementById("domain").value.trim() || "i7wap.xyz" });
        const text = await response.text();
        updateResults(text.split(/\r?\n/).filter(Boolean), false);
        setStatus("已载入现有有效邮箱：" + resultLines.length + " 条", "ok");
      } catch (err) {
        setStatus("失败：" + err.message, "bad");
      } finally {
        setBusy(false);
      }
    }

    generateButton.addEventListener("click", generateToPage);
    prepareDomainButton.addEventListener("click", async () => {
      setBusy(true);
      setStatus("正在同步域名", "warn");
      try {
        await prepareDomain();
        setStatus("域名同步完成，可以生成邮箱", "ok");
      } catch (err) {
        setStatus("失败：" + err.message, "bad");
      } finally {
        setBusy(false);
      }
    });
    exportButton.addEventListener("click", loadActiveMailboxes);
    copyButton.addEventListener("click", async () => {
      await navigator.clipboard.writeText(resultLines.join("\n"));
      setStatus("已复制当前结果：" + resultLines.length + " 条", "ok");
    });
    downloadButton.addEventListener("click", downloadCurrent);
    clearButton.addEventListener("click", () => {
      updateResults([], false);
      setStatus("已清空结果");
    });
  </script>
</body>
</html>"""


@app.get("/admin/mailboxes", response_model=list[MailboxOut])
def list_mailboxes(
    domain: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[Mailbox]:
    stmt = select(Mailbox).order_by(desc(Mailbox.id)).limit(limit)
    if domain:
        domain_obj = session.scalar(select(Domain).where(Domain.domain == domain.lower()))
        if not domain_obj:
            raise HTTPException(status_code=404, detail="domain_not_found")
        stmt = stmt.where(Mailbox.domain_id == domain_obj.id)
    if status:
        stmt = stmt.where(Mailbox.status == status)
    return list(session.scalars(stmt).all())


@app.post("/admin/mailboxes/{mailbox_id}/rotate-token", response_model=RotateTokenResponse)
def rotate_token(
    mailbox_id: int,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RotateTokenResponse:
    mailbox = session.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")
    domain = session.get(Domain, mailbox.domain_id)
    if not domain:
        raise HTTPException(status_code=500, detail="domain_missing")
    token = generate_token(settings.token_bytes)
    mailbox.token_hash = hash_value(token)
    mailbox.token_ciphertext = encrypt_value(token)
    session.add(mailbox)
    session.commit()
    return RotateTokenResponse(email=mailbox.email, token=token, api_url=build_api_url(domain.api_base_url, token, mailbox.email))


@app.post("/admin/mailboxes/{mailbox_id}/enable", response_model=MailboxOut)
def enable_mailbox(
    mailbox_id: int,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Mailbox:
    mailbox = session.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")
    _set_mailbox_backend_enabled(mailbox.email, True)
    mailbox.status = "active"
    session.add(mailbox)
    session.commit()
    session.refresh(mailbox)
    return mailbox


@app.post("/admin/mailboxes/{mailbox_id}/disable", response_model=MailboxOut)
def disable_mailbox(
    mailbox_id: int,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Mailbox:
    mailbox = session.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")
    _set_mailbox_backend_enabled(mailbox.email, False)
    mailbox.status = "disabled"
    session.add(mailbox)
    session.commit()
    session.refresh(mailbox)
    return mailbox


@app.post("/admin/messages", response_model=MessageOut)
def ingest_message(
    payload: MessageCreate,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Message:
    mailbox = session.scalar(select(Mailbox).where(Mailbox.email == str(payload.email).lower()))
    if not mailbox:
        raise HTTPException(status_code=404, detail="mailbox_not_found")
    body = "\n".join([payload.subject or "", payload.text_body or "", payload.html_body or ""])
    message = Message(
        mailbox_id=mailbox.id,
        message_id=payload.message_id,
        from_addr=payload.from_addr,
        to_addr=mailbox.email,
        subject=payload.subject,
        text_body=payload.text_body,
        html_body=payload.html_body,
        raw_path=payload.raw_path,
        code=extract_code(body),
        verified_link=extract_link(body),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


@app.get("/api/mail/code", response_model=MailCodeResponse)
def read_code(token: str, email: str, session: Session = Depends(get_session)) -> MailCodeResponse:
    mailbox = _mailbox_for_token_email(session, token, email)
    _refresh_mailbox_now(mailbox)
    message = _latest_message_for_mailbox(session, mailbox)
    return _mail_code_response(session, mailbox, message)


def _latest_message_for_mailbox(session: Session, mailbox: Mailbox) -> Message | None:
    return session.scalar(
        select(Message).where(Message.mailbox_id == mailbox.id).order_by(desc(Message.received_at), desc(Message.id))
    )


def _mail_code_response(session: Session, mailbox: Mailbox, message: Message | None) -> MailCodeResponse:
    if not message:
        return MailCodeResponse(ok=True, email=mailbox.email, found=False)
    code, link = _message_code_link(session, message)
    return MailCodeResponse(
        ok=True,
        email=mailbox.email,
        found=bool(code or link),
        code=code,
        link=link,
        subject=message.subject,
        received_at=message.received_at,
    )


def _message_code_link(session: Session, message: Message) -> tuple[str | None, str | None]:
    code, link = _code_link_for_message(message)
    if code != message.code or link != message.verified_link:
        message.code = code
        message.verified_link = link
        session.add(message)
        session.commit()
        session.refresh(message)
    return code, link


@app.get("/api/mail/messages", response_model=list[MessageOut])
def list_messages(
    token: str,
    email: str,
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[Message]:
    mailbox = _mailbox_for_token_email(session, token, email)
    _refresh_mailbox_now(mailbox)
    return list(
        session.scalars(
            select(Message)
            .where(Message.mailbox_id == mailbox.id)
            .order_by(desc(Message.received_at), desc(Message.id))
            .limit(limit)
        ).all()
    )


@app.get("/mail/{token}/{email}", response_model=None)
def read_code_link(
    token: str,
    email: str,
    request: Request,
    response_format: str | None = Query(default=None, alias="format", pattern="^(html|json)$"),
    session: Session = Depends(get_session),
) -> Response | MailCodeResponse:
    mailbox = _mailbox_for_token_email(session, token, email)
    _refresh_mailbox_now(mailbox)
    message = _latest_message_for_mailbox(session, mailbox)
    if response_format == "json" or (response_format is None and _accept_prefers_json(request)):
        return _mail_code_response(session, mailbox, message)
    code, link = _message_code_link(session, message) if message else (None, None)
    return HTMLResponse(_mail_detail_html(mailbox, message, code, link))


def _accept_prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept and "text/html" not in accept


def _refresh_mailbox_now(mailbox: Mailbox) -> None:
    if not settings.mail_read_refresh_enabled:
        return
    try:
        poll_mailbox(mailbox.id)
    except Exception:
        log.warning("mailbox read refresh failed email=%s", mailbox.email, exc_info=True)


def _mail_detail_html(mailbox: Mailbox, message: Message | None, code: str | None, link: str | None) -> str:
    if not message:
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(mailbox.email)} - 暂无邮件</title>
  {_mail_detail_style()}
</head>
<body>
  <main class="shell empty">
    <section class="notice">
      <div class="eyebrow">Mailbox System</div>
      <h1>{escape(mailbox.email)}</h1>
      <p>这个邮箱还没有收到邮件。</p>
      <a class="button" href="?format=json">查看 JSON</a>
    </section>
  </main>
</body>
</html>"""

    subject = message.subject or "(无主题)"
    from_addr = message.from_addr or "(未知发件人)"
    text_body = (message.text_body or "").strip()
    html_body = (message.html_body or "").strip()
    plain_body = text_body or visible_text(html_body).strip()
    received_at = _format_message_time(message.received_at)
    code_html = f'<span class="value code">{escape(code)}</span>' if code else '<span class="muted">未识别</span>'
    link_html = (
        f'<a class="value link" href="{escape(link, quote=True)}" target="_blank" rel="noreferrer noopener">{escape(link)}</a>'
        if link
        else '<span class="muted">无</span>'
    )
    content_panel = (
        f"""<section class="panel">
      <div class="panel-title">邮件内容</div>
      <iframe class="mail-frame" sandbox="" referrerpolicy="no-referrer" srcdoc="{escape(_safe_email_srcdoc(html_body), quote=True)}"></iframe>
    </section>"""
        if html_body
        else f"""<section class="panel">
      <div class="panel-title">邮件内容</div>
      {f'<pre class="mail-text">{escape(plain_body)}</pre>' if plain_body else '<div class="empty-block">这封邮件没有可显示的正文。</div>'}
    </section>"""
    )
    text_source_panel = (
        f"""<details class="panel source-panel">
      <summary>纯文本内容</summary>
      <pre class="mail-source">{escape(text_body)}</pre>
    </details>"""
        if html_body and text_body
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(subject)} - {escape(mailbox.email)}</title>
  {_mail_detail_style()}
</head>
<body>
  <main class="shell">
    <header class="mail-header">
      <div>
        <div class="eyebrow">完整邮件</div>
        <h1>{escape(subject)}</h1>
      </div>
      <a class="button" href="?format=json">JSON</a>
    </header>
    <section class="meta-grid" aria-label="邮件信息">
      <div><span>发件人</span><strong>{escape(from_addr)}</strong></div>
      <div><span>收件人</span><strong>{escape(message.to_addr)}</strong></div>
      <div><span>时间</span><strong>{escape(received_at)}</strong></div>
      <div><span>验证码</span>{code_html}</div>
      <div class="wide"><span>验证/登录链接</span>{link_html}</div>
    </section>
    {content_panel}
    {text_source_panel}
  </main>
</body>
</html>"""


def _safe_email_srcdoc(html_body: str) -> str:
    csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; base-uri 'none'; form-action 'none';"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp}\">"
        "</head><body>"
        f"{html_body}"
        "</body></html>"
    )


def _format_message_time(value: datetime | None) -> str:
    if not value:
        return "(未知时间)"
    return value.isoformat(sep=" ", timespec="seconds")


def _mail_detail_style() -> str:
    return """<style>
  :root {
    --bg: #eef3f7;
    --panel: #ffffff;
    --ink: #172331;
    --muted: #607182;
    --line: #cfd9e3;
    --accent: #2f87bd;
    --accent-dark: #226c99;
    --code-bg: #f2f7fb;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Microsoft YaHei", "Source Sans 3", sans-serif;
    font-size: 14px;
    letter-spacing: 0;
  }
  .shell {
    width: min(1440px, calc(100% - 32px));
    margin: 20px auto 36px;
  }
  .shell.empty {
    min-height: 80vh;
    display: grid;
    place-items: center;
  }
  .mail-header,
  .notice,
  .panel,
  .meta-grid {
    background: var(--panel);
    border: 1px solid var(--line);
    border-top: 3px solid var(--accent);
    box-shadow: 0 1px 2px rgba(23, 35, 49, 0.06);
  }
  .mail-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 20px;
  }
  h1 {
    margin: 4px 0 0;
    font-size: 24px;
    line-height: 1.3;
    font-weight: 700;
    overflow-wrap: anywhere;
  }
  .eyebrow {
    color: var(--accent-dark);
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 36px;
    padding: 0 14px;
    border: 1px solid #a8cbe5;
    border-radius: 4px;
    color: var(--accent-dark);
    background: #f8fcff;
    text-decoration: none;
    font-weight: 700;
    white-space: nowrap;
  }
  .button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .meta-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    margin-top: 14px;
    border-top-width: 1px;
  }
  .meta-grid div {
    min-width: 0;
    padding: 12px 14px;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }
  .meta-grid div:nth-child(4),
  .meta-grid .wide {
    border-right: 0;
  }
  .meta-grid .wide {
    grid-column: 1 / -1;
  }
  .meta-grid span,
  .panel-title {
    display: block;
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 6px;
  }
  .meta-grid strong,
  .value {
    display: block;
    min-width: 0;
    overflow-wrap: anywhere;
    font-weight: 700;
  }
  .code {
    width: fit-content;
    padding: 5px 9px;
    border-radius: 4px;
    background: var(--code-bg);
    border: 1px solid #d8e8f4;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 18px;
  }
  .link {
    color: var(--accent-dark);
  }
  .muted,
  .empty-block {
    color: var(--muted);
  }
  .panel {
    margin-top: 14px;
    padding: 14px 16px;
    border-top-width: 3px;
  }
  .mail-text,
  .mail-source {
    margin: 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: "JetBrains Mono", Consolas, monospace;
    line-height: 1.65;
  }
  .mail-frame {
    width: 100%;
    height: min(70vh, 720px);
    border: 1px solid var(--line);
    background: #ffffff;
  }
  details.panel summary {
    cursor: pointer;
    color: var(--accent-dark);
    font-weight: 700;
  }
  details.panel[open] summary {
    margin-bottom: 12px;
  }
  @media (max-width: 860px) {
    .shell {
      width: calc(100% - 20px);
      margin-top: 10px;
    }
    .mail-header {
      flex-direction: column;
    }
    .meta-grid {
      grid-template-columns: 1fr;
    }
    .meta-grid div {
      border-right: 0;
    }
  }
</style>"""


def _code_link_for_message(message: Message) -> tuple[str | None, str | None]:
    body = "\n".join([message.subject or "", message.text_body or "", message.html_body or ""])
    if not body.strip():
        return message.code, message.verified_link
    return extract_code(body) or message.code, extract_link(body)


def _mailbox_for_token_email(session: Session, token: str, email: str) -> Mailbox:
    normalized = email.lower()
    mailbox = session.scalar(select(Mailbox).where(Mailbox.email == normalized))
    if not mailbox or mailbox.status != "active":
        raise HTTPException(status_code=404, detail="mailbox_not_found")
    if not verify_value(token, mailbox.token_hash):
        raise HTTPException(status_code=403, detail="invalid_token_for_mailbox")
    return mailbox


def _generate_mailbox_credentials(
    session: Session, domain: Domain, prefix: str, reserved_local_parts: set[str] | None = None
) -> tuple[str, str, str, str]:
    local_part = _unique_local_part(session, domain.id, prefix, reserved_local_parts)
    email = f"{local_part}@{domain.domain}"
    token = generate_token(settings.token_bytes)
    password = generate_secret(settings.mailbox_secret_bytes)
    return local_part, email, token, password


def _sync_domain_with_mail_backend(domain: str) -> None:
    try:
        client = configured_mailu_client(
            enabled=settings.mailu_sync_enabled,
            api_url=settings.mailu_admin_api_url,
            api_token=settings.mailu_api_token,
            timeout_seconds=settings.mailu_timeout_seconds,
        )
        if client:
            client.ensure_domain(domain)
    except MailuClientError as exc:
        raise HTTPException(status_code=502, detail="mail_backend_domain_failed") from exc


def _sync_mailbox_with_mail_backend(
    *,
    email: str,
    password: str,
    quota_bytes: int,
    enable_imap: bool,
    enable_pop: bool,
    spam_enabled: bool,
) -> None:
    client = configured_mailu_client(
        enabled=settings.mailu_sync_enabled,
        api_url=settings.mailu_admin_api_url,
        api_token=settings.mailu_api_token,
        timeout_seconds=settings.mailu_timeout_seconds,
    )
    if not client:
        return
    client.create_user(
        email=email,
        password=password,
        quota_bytes=quota_bytes,
        enable_imap=enable_imap,
        enable_pop=enable_pop,
        spam_enabled=spam_enabled,
    )


def _set_mailbox_backend_enabled(email: str, enabled: bool) -> None:
    client = configured_mailu_client(
        enabled=settings.mailu_sync_enabled,
        api_url=settings.mailu_admin_api_url,
        api_token=settings.mailu_api_token,
        timeout_seconds=settings.mailu_timeout_seconds,
    )
    if client:
        try:
            client.set_user_enabled(email, enabled)
        except MailuClientError as exc:
            raise HTTPException(status_code=502, detail="mail_backend_update_failed") from exc


def _unique_local_part(
    session: Session, domain_id: int, prefix: str, reserved_local_parts: set[str] | None = None
) -> str:
    alphabet = string.ascii_lowercase + string.digits
    clean_prefix = "".join(ch for ch in prefix.lower() if ch.isalnum() or ch in {"-", "_"})
    clean_prefix = clean_prefix.strip("-_")
    reserved = reserved_local_parts or set()
    while True:
        suffix = secrets.choice(string.ascii_lowercase) + "".join(secrets.choice(alphabet) for _ in range(11))
        local_part = f"{clean_prefix}-{suffix}" if clean_prefix else suffix
        if local_part in reserved:
            continue
        exists = session.scalar(
            select(Mailbox.id).where(Mailbox.domain_id == domain_id, Mailbox.local_part == local_part)
        )
        if not exists:
            return local_part

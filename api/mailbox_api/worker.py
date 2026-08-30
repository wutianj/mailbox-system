from __future__ import annotations

import imaplib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email import policy
from email.message import Message as EmailMessage
from email.parser import BytesParser

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import Base, SessionLocal, engine
from .extractors import extract_code, extract_link
from .models import Mailbox, Message
from .security import decrypt_value

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mailbox-worker")
_last_mailbox_id = 0


def main() -> int:
    Base.metadata.create_all(bind=engine)
    while True:
        try:
            poll_once()
        except Exception:
            log.exception("poll cycle failed")
        time.sleep(settings.worker_poll_seconds)


def poll_once() -> None:
    mailbox_ids = _next_mailbox_ids(max(1, settings.worker_batch_size))
    if not mailbox_ids:
        return

    max_workers = max(1, min(settings.worker_concurrency, len(mailbox_ids)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(poll_mailbox, mailbox_id) for mailbox_id in mailbox_ids]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                log.exception("mailbox poll failed")


def _next_mailbox_ids(batch_size: int) -> list[int]:
    global _last_mailbox_id
    with SessionLocal() as session:
        ids = list(
            session.scalars(
                select(Mailbox.id)
                .where(Mailbox.status == "active", Mailbox.id > _last_mailbox_id)
                .order_by(Mailbox.id)
                .limit(batch_size)
            ).all()
        )
        if len(ids) < batch_size:
            ids.extend(
                session.scalars(
                    select(Mailbox.id)
                    .where(Mailbox.status == "active", Mailbox.id <= _last_mailbox_id)
                    .order_by(Mailbox.id)
                    .limit(batch_size - len(ids))
                ).all()
            )
    if ids:
        _last_mailbox_id = ids[-1]
    return ids


def poll_mailbox(mailbox_id: int) -> None:
    with SessionLocal() as session:
        mailbox = session.get(Mailbox, mailbox_id)
        if not mailbox or mailbox.status != "active":
            return
        email_addr = mailbox.email
        password = decrypt_value(mailbox.password_ciphertext)

    client: imaplib.IMAP4 | imaplib.IMAP4_SSL
    if settings.mailu_imap_ssl:
        client = imaplib.IMAP4_SSL(
            settings.mailu_imap_host,
            settings.mailu_imap_port,
            timeout=settings.mailu_imap_timeout_seconds,
        )
    else:
        client = imaplib.IMAP4(
            settings.mailu_imap_host,
            settings.mailu_imap_port,
            timeout=settings.mailu_imap_timeout_seconds,
        )
    try:
        client.login(email_addr, password)
        client.select("INBOX", readonly=True)
        status, search_data = client.uid("SEARCH", None, "ALL")
        if status != "OK" or not search_data:
            return
        uids = search_data[0].split()[-settings.worker_fetch_limit :]
        for uid in uids:
            raw_path = f"imap:INBOX:{uid.decode(errors='replace')}"
            with SessionLocal() as session:
                exists = session.scalar(
                    select(Message.id).where(Message.mailbox_id == mailbox_id, Message.raw_path == raw_path)
                )
            if exists:
                continue
            status, fetch_data = client.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = _first_raw_message(fetch_data)
            if not raw:
                continue
            parsed = _parse_message(raw)
            _store_message(mailbox_id, raw_path, parsed)
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _first_raw_message(fetch_data: list[bytes | tuple[bytes, bytes]]) -> bytes | None:
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _parse_message(raw: bytes) -> dict[str, str | None]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    text_body, html_body = _message_bodies(msg)
    combined = "\n".join(part for part in [msg.get("subject"), text_body, html_body] if part)
    return {
        "message_id": msg.get("message-id"),
        "from_addr": msg.get("from"),
        "subject": msg.get("subject"),
        "text_body": text_body,
        "html_body": html_body,
        "code": extract_code(combined),
        "verified_link": extract_link(combined),
    }


def _message_bodies(msg: EmailMessage) -> tuple[str | None, str | None]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            _append_part(part, text_parts, html_parts)
    else:
        _append_part(msg, text_parts, html_parts)
    return "\n".join(text_parts) or None, "\n".join(html_parts) or None


def _append_part(part: EmailMessage, text_parts: list[str], html_parts: list[str]) -> None:
    content_type = part.get_content_type()
    if content_type not in {"text/plain", "text/html"}:
        return
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True)
        if not payload:
            return
        charset = part.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
    if content_type == "text/html":
        html_parts.append(content)
    else:
        text_parts.append(content)


def _store_message(mailbox_id: int, raw_path: str, parsed: dict[str, str | None]) -> None:
    with SessionLocal() as session:
        mailbox = session.get(Mailbox, mailbox_id)
        if not mailbox:
            return
        message = Message(
            mailbox_id=mailbox_id,
            message_id=parsed["message_id"],
            from_addr=parsed["from_addr"],
            to_addr=mailbox.email,
            subject=parsed["subject"],
            text_body=parsed["text_body"],
            html_body=parsed["html_body"],
            raw_path=raw_path,
            code=parsed["code"],
            verified_link=parsed["verified_link"],
        )
        session.add(message)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


if __name__ == "__main__":
    raise SystemExit(main())

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mx_host: Mapped[str] = mapped_column(String(255))
    api_base_url: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    mailboxes: Mapped[list["Mailbox"]] = relationship(back_populates="domain_ref")


class Mailbox(Base):
    __tablename__ = "mailboxes"
    __table_args__ = (
        UniqueConstraint("domain_id", "local_part", name="uq_mailbox_domain_local"),
        Index("ix_mailboxes_token_hash", "token_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    local_part: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    password_ciphertext: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(255))
    token_ciphertext: Mapped[str] = mapped_column(Text)
    mail_backend_status: Mapped[str] = mapped_column(String(32), default="pending")
    mail_backend_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    domain_ref: Mapped[Domain] = relationship(back_populates="mailboxes")
    messages: Mapped[list["Message"]] = relationship(back_populates="mailbox")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "raw_path", name="uq_messages_mailbox_raw_path"),
        Index("ix_messages_mailbox_received", "mailbox_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailboxes.id"))
    message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    from_addr: Mapped[str | None] = mapped_column(String(320), nullable=True)
    to_addr: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_link: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    mailbox: Mapped[Mailbox] = relationship(back_populates="messages")


class MailboxEvent(Base):
    __tablename__ = "mailbox_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

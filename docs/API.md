# API Draft

## Public read API

```text
GET /api/mail/code?token=...&email=...
GET /api/mail/latest?token=...&email=...
GET /api/mail/messages?token=...&email=...
GET /api/mail/raw?token=...&email=...&id=...
```

## Admin API

```text
POST /admin/domains
GET  /admin/domains
POST /admin/mailboxes/batch
POST /admin/mailboxes/import
POST /admin/mailboxes/{id}/rotate-token
POST /admin/mailboxes/{id}/enable
POST /admin/mailboxes/{id}/disable
GET  /admin/export?domain=...
```

## Export Format

```text
email----api_url
```

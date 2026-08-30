# Modules

## 1. domain-center

- create and retire domains
- maintain MX/API base URLs
- validate DNS and mail routing state

## 2. account-center

- batch create mailboxes
- rotate tokens
- disable/enable accounts
- assign labels and groups

## 3. receive-pipeline

- receive SMTP mail
- persist raw MIME
- parse headers/body/attachments
- extract code and links

## 4. mail-api

- lookup by token
- list messages
- read latest mail
- export account-url pairs

## 5. admin-console

- view domains, mailboxes, messages, events
- batch operations
- health and audit

## 6. worker

- parse inbound messages
- cleanup expired accounts
- sync metrics and notifications

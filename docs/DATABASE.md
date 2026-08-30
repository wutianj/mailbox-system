# Database Draft

## domains

```sql
id bigserial primary key
domain text unique not null
mx_host text not null
api_base_url text not null
status text not null
default_policy jsonb not null default '{}'::jsonb
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

## mailboxes

```sql
id bigserial primary key
domain_id bigint not null references domains(id)
email text unique not null
local_part text not null
password_hash text not null
token_hash text not null
status text not null
label text
group_name text
expires_at timestamptz
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

## messages

```sql
id bigserial primary key
mailbox_id bigint not null references mailboxes(id)
message_id text
from_addr text
to_addr text
subject text
text_body text
html_body text
raw_path text
code text
verified_link text
received_at timestamptz not null default now()
```

## mailbox_events

```sql
id bigserial primary key
mailbox_id bigint references mailboxes(id)
action text not null
actor text
ip inet
detail jsonb not null default '{}'::jsonb
created_at timestamptz not null default now()
```

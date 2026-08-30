# Source Selection

## Decision

Use `Mailu` as the primary upstream source.

GitHub:

```text
https://github.com/Mailu/Mailu
```

Local upstream path:

```text
D:\yewu\mailbox-system\upstream\Mailu
```

Current reviewed commit:

```text
d95e2c3 Merge #4084
```

## Why Mailu Is Primary

- mature Docker mail platform
- MIT license
- Python/Flask admin service
- existing domain, user, alias, token models
- REST API under `core/admin/mailu/api`
- internal Postfix/Dovecot integration routes
- per-user token and anonymous alias code already exists
- easier to extend with custom mailbox-code API than PHP-heavy stacks

Relevant local code:

```text
upstream\Mailu\core\admin\mailu\models.py
upstream\Mailu\core\admin\mailu\schemas.py
upstream\Mailu\core\admin\mailu\api\v1\user.py
upstream\Mailu\core\admin\mailu\api\v1\domain.py
upstream\Mailu\core\admin\mailu\api\v1\alias.py
upstream\Mailu\core\admin\mailu\api\v1\token.py
upstream\Mailu\core\admin\mailu\internal\views\postfix.py
upstream\Mailu\core\admin\mailu\internal\views\dovecot.py
upstream\Mailu\docs\anonmail.rst
```

## Secondary References

### mailcow-dockerized

GitHub:

```text
https://github.com/mailcow/mailcow-dockerized
```

Local path:

```text
D:\yewu\mailbox-system\upstream\mailcow-dockerized
```

Reviewed commit:

```text
02552ff Merge pull request #7427 from mailcow/staging
```

Use for:

- operations console ideas
- OpenAPI coverage
- mailbox/domain API endpoint shape
- logs, quarantine, queue operations, monitoring

Do not use as primary fork because it is GPL-3.0 and much larger.

### Modoboa

GitHub:

```text
https://github.com/modoboa/modoboa
```

Local path:

```text
D:\yewu\mailbox-system\upstream\modoboa
```

Reviewed commit:

```text
63d2370 fix(webmail): reliable unread counters in the mailbox sidebar (#4138)
```

Use for:

- Django admin patterns
- mailbox ownership checks
- audit/API token patterns

Do not use as primary fork because Mailu is closer to our Docker mail stack target.

### Inbucket

GitHub:

```text
https://github.com/inbucket/inbucket
```

Local path:

```text
D:\yewu\mailbox-system\upstream\inbucket
```

Reviewed commit:

```text
e56c6d5 Revert "build(deps): bump svgo from 2.8.0 to 2.8.2 in /ui (#596)"
```

Use for:

- message storage/query API shape
- mailbox naming and message parsing ideas

Do not use as primary fork because it is catch-all temporary mail, not real-account mailbox isolation.

### docker-mailserver

GitHub:

```text
https://github.com/docker-mailserver/docker-mailserver
```

Local path:

```text
D:\yewu\mailbox-system\upstream\docker-mailserver
```

Reviewed commit:

```text
c9b015d chore: update base image from Debian 12 to Debian 13 (#4536)
```

Use as fallback if we abandon Mailu and build a custom stack from lower-level mail primitives.

### mailserver-admin

GitHub:

```text
https://github.com/jeboehm/mailserver-admin
```

Local path:

```text
D:\yewu\mailbox-system\upstream\mailserver-admin
```

Reviewed commit:

```text
d8bf57d chore(deps): update postgres:18-alpine docker digest to d3e1620 (#498)
```

Use for:

- domain/user/alias admin model reference
- docker-mailserver management reference

Do not use as primary because Mailu already includes a broader mail platform.

## Target Customization

Extend Mailu with a new service/module named `mailbox-code-api`.

Required additions:

- per-mailbox token table
- batch mailbox generation
- export `email----api_url`
- endpoint `GET /mail/<token>/<email>`
- endpoint `GET /api/mail/code?token=...&email=...`
- token resolves to exactly one Mailu user/mailbox
- URL email must match token-bound mailbox
- domain is configurable and not hardcoded

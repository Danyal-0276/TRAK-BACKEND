# Default admin accounts

Three accounts are **always** treated as admins (API `role=admin`, app admin screens, Django `is_staff` when seeded):

| Login email | Notes |
|-------------|--------|
| `danyal@admin.com` | Built-in |
| `shahroz@admin.com` | Built-in |
| `abdullah@admin.com` | Built-in |

They are included in `ADMIN_EMAILS` in settings, so **self-registration** with one of these addresses also receives the `admin` role (after email confirmation of your product policy, if you add that later).

## Shared password (initial setup)

Set a password once and create/update all three in the database:

```bash
# Windows PowerShell
$env:SEED_ADMIN_PASSWORD = "YourStrongSharedSecret123!"
python manage.py seed_default_admins
```

Or one-off:

```bash
python manage.py seed_default_admins --password "YourStrongSharedSecret123!"
```

Requirements:

1. **MongoDB must be running** (djongo uses the same `TRAK_DB` as in `settings.py`).
2. Run migrations first: `python manage.py migrate`.

The command creates missing admins and resets their password to the value you provide, upgrades `role` to `admin`, and sets `is_staff=True` for Django admin login if you use it.

## Additional admins via env

Optional comma-separated **`ADMIN_EMAILS`** in `.env` adds more addresses that receive the `admin` role **when those users register** via `/api/auth/register/`.  
The **seed** command only touches the three built-in `@admin.com` accounts above, not every `ADMIN_EMAILS` entry.

## Diagnostics

```bash
python manage.py trak_diagnostics
```

Runs `manage.py check`, lists users/admins in Django, and pings MongoDB for article collections.

## Production

- Do not commit `SEED_ADMIN_PASSWORD`.
- Change passwords after first deploy; prefer individual strong passwords per person in production.
- Consider removing or narrowing `ADMIN_EMAILS` to real corporate addresses only.

# Authentication & REST API (JWT)

All URLs are relative to the server origin (e.g. `http://127.0.0.1:8000`).

## Public

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/auth/register/` | `email`, `password`, `password_confirm` | `access`, `refresh`, `user` |
| POST | `/api/auth/login/` | `email`, `password` | `access`, `refresh`, `user` (+ token payload) |
| POST | `/api/auth/token/refresh/` | `refresh` | `access` |
| POST | `/api/auth/password-reset/` | `email` | Always `200` + generic message (email sent if user exists) |
| POST | `/api/auth/password-reset/confirm/` | `uid`, `token`, `password`, `password_confirm` | `200` when password updated |

`user` shape: `{ id, email, role, created_at }` — `role` is `admin` or `user`. Admins are any email listed in `ADMIN_EMAILS` (includes built-in `danyal@admin.com`, `shahroz@admin.com`, `abdullah@admin.com`). See [README-DEFAULT-ADMINS.md](README-DEFAULT-ADMINS.md).

## Authenticated (header: `Authorization: Bearer <access>`)

| Method | Path | Description |
|--------|------|---------------|
| GET | `/api/auth/me/` | Current user profile |
| GET | `/api/user/feed/?limit=50&q=` | Personalized feed (user **keywords** ∩ article text + **topic_keywords** + entities); optional **q** substring filter |
| POST | `/api/user/track-keywords/` | JSON `{ "keywords": ["foo", "bar"] }` — upserts Mongo `user_keywords` |
| GET | `/api/user/articles/<article_id>/` | Article detail (`_id` or `canonical_url`) |

## Admin only (`role=admin`, same JWT)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/articles/?scope=raw|processed|all&page=1&page_size=20` | Lists articles |
| GET | `/api/admin/analytics/` | Counts by pipeline status & credibility label |
| POST | `/api/admin/pipeline/run/` | JSON `{ "limit": 10 }` — runs batch AI pipeline |

## Legacy

- `/api/accounts/health/` — health probe
- `/api/news/health/`, `/api/admin-panel/health/` — unchanged

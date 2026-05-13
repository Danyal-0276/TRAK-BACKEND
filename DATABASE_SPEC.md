# DATABASE SPECIFICATION (TRAK Backend)

## 1) Scope and Sources

This specification is generated from:

- Local code scan of Django settings, models, and Mongo data-access code.
- Live MongoDB introspection using the configured `MONGODB_URI` and database `TRAK_DB`.

Focus area requested: accounts/users modules and their database interaction.

---

## 2) Architecture Overview

TRAK uses a hybrid persistence architecture:

1. Django auth/account entities (`accounts.User` and related auth tables) stored via Django ORM.
2. News, personalization, social, and notification features stored in MongoDB collections through `pymongo`.
3. User linkage across SQL-style Django entities and Mongo documents is done through `user_id` (application-level reference), not database-enforced foreign keys in Mongo.

### Runtime Database Mode

- `AUTH_USER_MODEL = "accounts.User"`
- Database engine is selected by `DJANGO_DB_ENGINE`:
  - `djongo` -> Django ORM collections in MongoDB.
  - otherwise -> Django ORM in SQLite.
- Independent of ORM engine, app features still use direct `pymongo` collections configured by:
  - `MONGODB_URI`
  - `MONGODB_RAW_DATABASE` (default: `TRAK_DB`)
  - per-collection env variables (`MONGODB_RAW_COLLECTION`, etc.)

---

## 3) Entity Catalog (Local Architecture)

## A. Core Identity and Access (Django)

### Entity: `accounts_user` (Django model `accounts.User`)

- `id`: BigAutoField, PK, unique, not null
- `email`: EmailField, unique, not null
- `role`: CharField(16), choices: `admin | user`, default `user`
- `created_at`: DateTime, auto_now_add
- `is_staff`: Boolean, default false
- `is_active`: Boolean, default true
- `password`: inherited from `AbstractBaseUser`
- `last_login`: inherited from `AbstractBaseUser`, nullable
- `is_superuser`: inherited from `PermissionsMixin`

Constraints:

- Primary key on `id`
- Unique on `email`

Relationships:

- Many-to-many with `auth_group` through `accounts_user_groups`
- Many-to-many with `auth_permission` through `accounts_user_user_permissions`

---

### Entity: `auth_group`

- `id`: PK
- `name`: unique

---

### Entity: `auth_permission`

- `id`: PK
- `name`
- `content_type_id`
- `codename`

Constraints:

- Unique composite (`content_type_id`, `codename`)

---

### Entity: `accounts_user_groups` (join)

- `id`: PK
- `user_id`
- `group_id`

Constraints:

- Unique composite (`user_id`, `group_id`)

---

### Entity: `accounts_user_user_permissions` (join)

- `id`: PK
- `user_id`
- `permission_id`

Constraints:

- Unique composite (`user_id`, `permission_id`)

---

## B. User Domain Collections (Mongo)

### Collection: `user_profiles`

Representative attributes:

- `user_id` (int) [unique]
- `full_name` (string)
- `username` (string)
- `phone` (string)
- `email_verified` (bool)
- `phone_verified` (bool)
- `bio` (string)
- `avatar_image` (string)
- `followers_count` (int; derived)
- `following_count` (int; derived)

Indexes:

- unique index on `user_id`

---

### Collection: `user_follows`

Attributes:

- `follower_user_id` (int)
- `followed_user_id` (int)

Indexes/constraints:

- unique composite (`follower_user_id`, `followed_user_id`)
- index on `follower_user_id`
- index on `followed_user_id`

Relationship semantics:

- Self-referential many-to-many over users.

---

### Collection: `user_keywords`

Attributes:

- `user_id` (int) [unique]
- `keywords` (array<string>)
- `created_at` (datetime)
- `updated_at` (datetime)

Indexes:

- unique index on `user_id`

---

### Collection: `chatbot_history`

Attributes:

- `user_id` (int) [unique]
- `messages` (array<object>)
  - `role` (user|bot)
  - `text` (string)
  - optional article metadata (`article_title`, `article_url`, `source`)

Indexes:

- unique index on `user_id`

---

### Collection: `user_preferences`

Attributes (observed union across modules):

- `user_id` (int) [unique]
- `notifications_enabled` (bool)
- `dark_mode_enabled` (bool)
- `personalization_enabled` (bool)
- `push_enabled` (bool)
- `email_enabled` (bool)
- `keyword_alerts` (bool)
- `updated_at` (datetime)

Indexes:

- unique index on `user_id`

---

### Collection: `device_tokens`

Attributes:

- `user_id` (int)
- `token` (string)
- `platform` (string)
- `created_at` (datetime)
- `updated_at` (datetime)

Indexes:

- unique composite (`user_id`, `token`)

---

### Collection: `notifications`

Attributes:

- `user_id` (int)
- `type` (string)
- `text` (string)
- `details` (string)
- `keyword` (string|null)
- `read` (bool)
- `important` (bool)
- `meta` (object)
- `created_at` (datetime)
- `updated_at` (datetime)

Indexes:

- composite (`user_id`, `created_at`)
- composite (`user_id`, `read`)

---

### Collection: `bookmarks`

Attributes:

- `user_id` (int)
- `article_id` (string)
- `title` (string)
- `url` (string)
- `created_at` (datetime)

Indexes:

- unique composite (`user_id`, `article_id`)

---

### Collection: `reactions`

Attributes:

- `user_id` (int)
- `article_id` (string)
- `reaction` (enum-like string: `like|dislike`)
- `updated_at` (datetime)

Indexes:

- unique composite (`user_id`, `article_id`)

---

## C. News Pipeline Collections (Mongo)

### Collection: `raw_articles`

Attributes:

- `canonical_url` (string) [unique]
- `source_key` (string)
- `title` (string)
- `summary` (string|null)
- `body_text` (string)
- `published_at` (datetime|string|null)
- `author_name` (string|null)
- `author_url` (string|null)
- `category` (string|null)
- `image_url` (string|null)
- `http_status` (int)
- `content_type` (string)
- `extra` (object; includes links/site display name)
- `pipeline_status` (pending|processing|done|failed)
- `fetched_at` (datetime)
- optional `raw_html` (string)

Indexes:

- unique on `canonical_url`
- (`source_key`, `fetched_at`)
- `pipeline_status`
- `published_at`
- `title`

---

### Collection: `processed_articles`

Attributes:

- `canonical_url` (string) [unique, sparse]
- `raw_canonical_url` (string)
- `title` (string)
- `source_key` (string)
- `published_at` (datetime|string|null)
- `clean_text` (string)
- `normalized_text` (string)
- `normalized_terms` (array<string>)
- `summary` (string)
- `entities` (array<object>)
- `topic_keywords` (array<string>)
- `processed_at` (datetime)
- `language` (string)
- `model_versions` (object)
- credibility outputs:
  - `credibility_label`
  - `credibility_probs`
  - `credibility_max_prob`
  - `credibility_model_id`
  - `credibility_labels_map`

Indexes:

- unique sparse on `canonical_url`
- `raw_canonical_url`
- `processed_at`
- `credibility_label`
- `topic_keywords`

Note on transformer-related storage:

- Transformer inference outputs are stored in `processed_articles` (`credibility_*`, `model_versions`) plus NLP-derived fields (`entities`, `topic_keywords`, `normalized_terms`).
- No vector embedding collection/index was found.

---

## 4) Live MongoDB Snapshot (Remote)

Live query result (`TRAK_DB` at generation time):

- Connected successfully: **true**
- Collections observed:
  - `accounts_user` (2 docs)
  - `accounts_user_groups`
  - `accounts_user_user_permissions`
  - `auth_group`
  - `auth_group_permissions`
  - `auth_permission`
  - `authtoken_token`
  - `django_admin_log`
  - `django_content_type`
  - `django_migrations`
  - `django_session`
  - `__schema__`

Interpretation:

- Remote currently contains Django-auth and Django-meta collections.
- Feature collections such as `raw_articles`, `processed_articles`, `user_profiles`, `bookmarks`, `notifications`, etc., are part of the local architecture but were not yet present in the inspected remote dataset at query time.

---

## 5) Relationships (Conceptual ER)

```mermaid
erDiagram
    ACCOUNTS_USER {
      bigint id PK
      string email UK
      string role
      datetime created_at
      bool is_staff
      bool is_active
      bool is_superuser
      datetime last_login
      string password
    }

    AUTH_GROUP {
      int id PK
      string name UK
    }

    AUTH_PERMISSION {
      int id PK
      string codename
      int content_type_id
    }

    ACCOUNTS_USER_GROUPS {
      int id PK
      bigint user_id FK
      int group_id FK
    }

    ACCOUNTS_USER_USER_PERMISSIONS {
      int id PK
      bigint user_id FK
      int permission_id FK
    }

    USER_PROFILES {
      objectid _id PK
      bigint user_id UK
      string username
      string phone
      bool email_verified
      bool phone_verified
    }

    USER_FOLLOWS {
      objectid _id PK
      bigint follower_user_id
      bigint followed_user_id
    }

    USER_KEYWORDS {
      objectid _id PK
      bigint user_id UK
      array_keywords keywords
    }

    CHATBOT_HISTORY {
      objectid _id PK
      bigint user_id UK
      array_messages messages
    }

    USER_PREFERENCES {
      objectid _id PK
      bigint user_id UK
    }

    DEVICE_TOKENS {
      objectid _id PK
      bigint user_id
      string token
    }

    NOTIFICATIONS {
      objectid _id PK
      bigint user_id
      bool read
      datetime created_at
    }

    BOOKMARKS {
      objectid _id PK
      bigint user_id
      string article_id
    }

    REACTIONS {
      objectid _id PK
      bigint user_id
      string article_id
      string reaction
    }

    RAW_ARTICLES {
      objectid _id PK
      string canonical_url UK
      string source_key
      string title
      string pipeline_status
    }

    PROCESSED_ARTICLES {
      objectid _id PK
      string canonical_url UK
      string raw_canonical_url
      string credibility_label
      array_keywords topic_keywords
    }

    ACCOUNTS_USER ||--o{ ACCOUNTS_USER_GROUPS : has
    AUTH_GROUP ||--o{ ACCOUNTS_USER_GROUPS : assigns

    ACCOUNTS_USER ||--o{ ACCOUNTS_USER_USER_PERMISSIONS : has
    AUTH_PERMISSION ||--o{ ACCOUNTS_USER_USER_PERMISSIONS : grants

    ACCOUNTS_USER ||--|| USER_PROFILES : profile_by_user_id
    ACCOUNTS_USER ||--o{ USER_KEYWORDS : preferences_keywords
    ACCOUNTS_USER ||--o{ CHATBOT_HISTORY : chat_history
    ACCOUNTS_USER ||--o{ USER_PREFERENCES : settings
    ACCOUNTS_USER ||--o{ DEVICE_TOKENS : push_devices
    ACCOUNTS_USER ||--o{ NOTIFICATIONS : receives
    ACCOUNTS_USER ||--o{ BOOKMARKS : bookmarks
    ACCOUNTS_USER ||--o{ REACTIONS : reacts

    ACCOUNTS_USER ||--o{ USER_FOLLOWS : follower_user_id
    ACCOUNTS_USER ||--o{ USER_FOLLOWS : followed_user_id

    RAW_ARTICLES ||--o| PROCESSED_ARTICLES : canonical_url
  ```

---

## 6) Django-to-Mongo Interaction Map

1. Auth and identity are handled by Django model `accounts.User`.
2. API endpoints in `accounts`, `news`, and `notifications` call Mongo collections directly using `pymongo`.
3. User context joins happen in application code:
   - Django user PK (`request.user.pk`) is persisted in Mongo as `user_id`.
   - Reads/writes combine ORM user data + Mongo profile/preferences/follow state.
4. Pipeline flow:
   - Scraper writes `raw_articles`.
   - Orchestrator processes raw docs and upserts into `processed_articles`.
   - User-facing feeds query processed docs, with raw fallback.

---

## 7) Constraints, Risks, and Notes

- Mongo relationships are not FK-enforced; referential integrity depends on app logic.
- Some count fields in profile (`followers_count`, `following_count`) are derived while live counts are recomputed from `user_follows`; treat stored counters as cache-like.
- `user_preferences` is shared by both `news` and `notifications` features, so schema is additive.
- If you require strict remote/live schema parity, run index bootstrapping (for example via `ensure_all_article_indexes`) and feature traffic seeding before re-scanning.

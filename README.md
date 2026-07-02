# CollabDocs---Collaborative-Document-Platform

A project used for Collaborative Document Platform

# CollabDocs — Collaborative Document Platform

A backend API for a collaborative document platform built with Django REST Framework and PostgreSQL.
Think of it as a simplified Notion/Google Docs — API only, Postman is the client.

---

## What this project covers

- Custom User model with UUID primary keys
- Workspaces with role-based membership (admin/editor/viewer)
- Documents with full version history (every save = new snapshot)
- Threaded comments (self-referential FK)
- Tag system with ManyToMany relationships
- Automatic audit logging via Django signals
- Request logging middleware
- Query optimisation with select_related, annotate, Q objects

---

## Tech Stack

- Python 3.12
- Django 5.0.6
- Django REST Framework 3.15.1
- PostgreSQL 16 (via Docker)
- psycopg2-binary, python-dotenv, django-filter

---

## Project Structure

CollabDocs/

├── api/

│ ├── migrations/

│ ├── models.py # 8 models

│ ├── serializers.py # all serializers with validation

│ ├── views.py # all ViewSets and endpoints

│ ├── urls.py # router registration

│ ├── signals.py # post_save signal for AuditLog

│ ├── middleware.py # request logging middleware

│ └── apps.py # signal registration via ready()

├── collabdocs/

│ ├── settings.py

│ └── urls.py

├── .env.example

├── requirements.txt

├── docker-compose.yml

└── README.md

---

## Setup Instructions

### 1. Clone the repository

```bash
git clone https://github.com/mohammaedsuhan/CollabDocs---Collaborative-Document-Platform.git
cd CollabDocs---Collaborative-Document-Platform
```

### 2. Create and activate virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
# Copy the example file and fill in your values
cp .env.example .env
```

Edit `.env` with your values:
SECRET_KEY=your-secret-key-here

DEBUG=True

DB_NAME=collabdocs_db

DB_USER=collabdocs_user

DB_PASSWORD=your-password-here

DB_HOST=localhost

DB_PORT=5432

### 5. Start PostgreSQL with Docker

```bash
docker compose up -d
```

Verify it's running:

```bash
docker ps
```

You should see `collabdocs-db-1` with status `Up (healthy)`.

### 6. Apply migrations

```bash
python manage.py migrate
```

### 7. Run the development server

```bash
python manage.py runserver
```

API is now live at `http://127.0.0.1:8000/api/`

---

## API Endpoints

### Users

| Method | Endpoint           | Description    |
| ------ | ------------------ | -------------- |
| POST   | `/api/users/`      | Create a user  |
| GET    | `/api/users/{id}/` | Get user by ID |

### Workspaces

| Method | Endpoint                        | Description                                  |
| ------ | ------------------------------- | -------------------------------------------- |
| POST   | `/api/workspaces/`              | Create workspace (owner auto-added as admin) |
| GET    | `/api/workspaces/{id}/`         | Get workspace with member count              |
| POST   | `/api/workspaces/{id}/members/` | Add member with role                         |
| GET    | `/api/workspaces/{id}/members/` | List all members                             |
| GET    | `/api/workspaces/{id}/summary/` | Doc/member/comment counts                    |

### Documents

| Method | Endpoint                        | Description                                             |
| ------ | ------------------------------- | ------------------------------------------------------- |
| POST   | `/api/documents/`               | Create document + first version (atomic)                |
| PUT    | `/api/documents/{id}/`          | Update document + new version (atomic)                  |
| GET    | `/api/documents/`               | List docs with filters (workspace, status, tag, search) |
| GET    | `/api/documents/{id}/versions/` | All versions in order                                   |
| GET    | `/api/documents/{id}/stats/`    | Version/comment/contributor counts                      |
| POST   | `/api/documents/{id}/tags/`     | Add tags to document                                    |

### Comments

| Method | Endpoint                       | Description                    |
| ------ | ------------------------------ | ------------------------------ |
| POST   | `/api/comments/`               | Add top-level comment or reply |
| GET    | `/api/comments/?document={id}` | List comments for a document   |

### Tags

| Method | Endpoint     | Description  |
| ------ | ------------ | ------------ |
| POST   | `/api/tags/` | Create a tag |

### Audit Logs

| Method | Endpoint           | Description                                         |
| ------ | ------------------ | --------------------------------------------------- |
| GET    | `/api/audit-logs/` | Audit logs filtered by actor ID, action, date range |

---

## Key Technical Decisions

**Transactions**
Workspace creation wraps workspace + member add in `transaction.atomic()`.
Document save wraps document + version creation in `transaction.atomic()`.
If either operation fails, both roll back — no orphaned or inconsistent data.

**Signals**
A `post_save` signal on `Document` automatically writes an `AuditLog` entry
on every create and update — regardless of which part of the codebase triggers the save.
Registered in `ApiConfig.ready()` via `api.apps.ApiConfig` in `INSTALLED_APPS`.

**Middleware**
`RequestLoggingMiddleware` wraps every request and prints method, path,
status code, and time taken in milliseconds to the console.

**Query Optimisation**

- `select_related` on all endpoints returning nested user/workspace data
- `annotate()` with `Count` for member_count, version_count, comment aggregations
- `Q` objects for OR filtering on document list (title OR content search)
- `values_list()` with `distinct()` for contributor count

---

## Environment Variables

| Variable      | Description                                            |
| ------------- | ------------------------------------------------------ |
| `SECRET_KEY`  | Django secret key                                      |
| `DEBUG`       | True for development, False for production             |
| `DB_NAME`     | PostgreSQL database name                               |
| `DB_USER`     | PostgreSQL username                                    |
| `DB_PASSWORD` | PostgreSQL password                                    |
| `DB_HOST`     | Database host (localhost when running Django natively) |
| `DB_PORT`     | Database port (default 5432)                           |

---

## Postman Collection

Import `CollabDocs.postman_collection.json` from the repository root into Postman.
All 17 endpoints are organised into folders with sample request bodies.

---

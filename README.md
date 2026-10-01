# CrisisCommand AI

CrisisCommand AI is a Flask + MySQL emergency coordination and resource-management platform for a hackathon demonstration.

## Core workflow

Emergency report → AI assessment → human crisis approval → nearby hospital coordination → hospital resource response → AI resource allocation → human allocation approval → hospital notification/dispatch → resource tracking → operation completion.

The system is designed as decision support: AI proposes assessments and allocations, while human authorization is required for critical workflow decisions.

## Key features

- Emergency reporting with map/location support
- Incident severity and resource assessment
- Human crisis approval/rejection
- Hospital registration and verification workflow
- Hospital resource registration and status management
- Hospital accept / partial accept / reject responses
- Explainable AI resource allocation
- Human allocation approval/rejection
- Hospital allocation notifications
- Active-plan and resource tracking
- Admin command center, audit trail and notifications
- First-aid guidance
- Forgot-password flow with optional SMTP
- Dynamic replanning support
- Live/periodic command-center updates
- **Ambulance rule:** 1 ambulance for every maximum 2 affected people (`ceil(affected_people / 2)`)

## Required user input rules

- Contact information is **mandatory** and validated.
- Emergency description is **mandatory** and must contain **5–2,000 characters**.
- Password must contain **8–12 characters**.
- Authentication, CSRF protection, database checks and workflow/state checks remain enabled.

## Local setup

### 1. Create a virtual environment

```bash
python -m venv venv
```

### 2. Activate it on Windows

```powershell
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure MySQL

Create a MySQL database named `crisiscommand` and import:

```text
database/crisiscommand.sql
```

Then create `.env` from `.env.example` and set your local MySQL credentials.

### 5. Run

```bash
python run.py
```

Open:

```text
http://127.0.0.1:5000
```

## Demo credentials

The demo admin credentials are configurable through `.env`:

```text
Admin email: admin@crisiscommand.local
Admin password: demo123
```

Do not use demo credentials for a real production system.

## Railway deployment

1. Push this repository to a **public GitHub repository**.
2. Create a Railway project.
3. Deploy the repository as a web service.
4. Add a Railway MySQL service.
5. Configure the application's database variables. The app accepts either the project's `MYSQL_*` variables or Railway's `MYSQLHOST`, `MYSQLPORT`, `MYSQLUSER`, `MYSQLPASSWORD`, and `MYSQLDATABASE`.
6. Import `database/crisiscommand.sql` into the Railway MySQL database.
7. Set a strong `SECRET_KEY`.
8. Set `SESSION_COOKIE_SECURE=true` when serving the application over HTTPS.
9. Railway uses `python run.py`; the launcher automatically uses the platform's `PORT`.
10. The health endpoint is:

```text
/health
```

## GitHub safety

Do **not** commit:

- `.env`
- passwords or API keys
- SMTP credentials
- local virtual environments
- Python cache files

The included `.gitignore` is configured for these items.

## Demo flow

For the hackathon demo, show:

1. Submit an emergency.
2. Admin reviews and approves it.
3. Nearby hospitals receive a resource request.
4. Hospitals submit available resources.
5. AI proposes an allocation.
6. Admin approves the allocation.
7. Hospital receives the allocation and dispatch information.
8. Track the active response.
9. Complete the operation and show the resource returning to availability.
10. Demonstrate the ambulance calculation with different affected-person counts.

## Project structure

```text
CrisisCommandAI/
├── agents/
├── api/
├── database/
├── docs/
├── services/
├── static/
├── templates/
├── app.py
├── config.py
├── run.py
├── requirements.txt
├── Procfile
├── railway.toml
└── README.md
```

## Important

This repository is a hackathon/demo system for emergency coordination and decision support. It should not be treated as a substitute for emergency services or professional medical/dispatch decisions.

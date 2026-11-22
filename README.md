# Personal Expense Tracker

A single-user personal finance application built with Python, Streamlit, and
SQLite. Track income and expenses, review budgets and trends, manage recurring
templates, and export transaction data and monthly reports.

The app is designed for local personal use. It does not provide user
registration, per-user access control, bank connections, or durable cloud
storage.

## Screenshots

Screenshots are not included yet. Add reviewed, privacy-safe captures before
publishing:

| Page | Screenshot |
| --- | --- |
| Overview | _Screenshot placeholder — redact all personal financial data._ |
| Transactions | _Screenshot placeholder — use synthetic or redacted data._ |
| Budgets & Insights | _Screenshot placeholder — use synthetic or redacted data._ |
| Analytics | _Screenshot placeholder — use synthetic or redacted data._ |
| Recurring & Calendar | _Screenshot placeholder — use synthetic or redacted data._ |
| Reports | _Screenshot placeholder — use synthetic or redacted data._ |

## Features

- **Overview:** lifetime income, expenses, and net summary.
- **Transactions:** add, edit, and confirmed-delete income or expenses; manage
  tags; search and filter by date, category, and type; review paginated history;
  export filtered transactions to CSV.
- **Budgets & Insights:** manage monthly overall and category budgets, view
  progress and thresholds, and review savings indicators, recommendations,
  category trends, and unusual-expense alerts.
- **Analytics:** custom date ranges, income and expense summaries, monthly,
  weekly, and yearly comparisons, category breakdowns, and interactive charts.
- **Recurring & Calendar:** create weekly or monthly templates, activate or
  delete them, explicitly process due occurrences, and view transactions by
  calendar month.
- **Reports:** generate and download a monthly PDF report with confirmed
  totals, category expenses, budget performance, and available insights.
- **Authentication:** one shared password, stored as a salted scrypt hash in
  local secrets or a hosting secret, with a 30-minute session timeout and
  Logout control.
- **Database safety:** SQLite initialization and migrations, integer-paisa
  money storage, and explicit backup and restore helpers.

Recurring transactions are not generated automatically. Use **Process Due
Recurring Transactions** to create due occurrences; the database operation
protects against duplicate executions.

## Technology

- Python 3.11 or newer (Python 3.12 is a suitable local/Community Cloud target)
- Streamlit multipage navigation (`st.navigation` / `st.Page`)
- SQLite through Python's built-in `sqlite3` module
- Pandas for in-memory analytics
- Plotly for charts
- ReportLab for PDF generation
- `unittest` for automated tests

All direct third-party dependencies and compatible version ranges are listed in
[requirements.txt](requirements.txt). No separate test-only package is
required.

## Architecture

- [app.py](app.py) configures the app, enforces authentication before page
  navigation, and defines the six destinations.
- [pages/](pages/) contains Streamlit presentation code. Pages use the
  existing services rather than duplicating financial calculations.
- [expense_tracker/database.py](expense_tracker/database.py) owns SQLite
  persistence, migrations, transaction validation, budgets, recurring
  execution, and backup/restore operations.
- [expense_tracker/analytics.py](expense_tracker/analytics.py) prepares
  transaction data and calculates summaries and aggregates.
- [expense_tracker/intelligence.py](expense_tracker/intelligence.py) provides
  local, explainable financial insights.
- [expense_tracker/reports.py](expense_tracker/reports.py) creates PDF reports
  in memory.
- [expense_tracker/auth.py](expense_tracker/auth.py) implements the
  single-user password setup/login, session expiry, and Logout.
- [expense_tracker/ui/common.py](expense_tracker/ui/common.py) contains shared
  cached database access and presentation helpers.
- [tests/](tests/) contains the automated test suite.

### Project structure

```text
Personal_Expense_Tracker/
├── .streamlit/
│   └── config.toml                 # Theme and local Streamlit settings
├── docs/
│   └── DATABASE_BACKUP.md          # Backup and restore instructions
├── expense_tracker/
│   ├── analytics.py
│   ├── auth.py
│   ├── database.py
│   ├── intelligence.py
│   ├── reports.py
│   └── ui/
│       └── common.py
├── pages/
│   ├── Overview.py
│   ├── Transactions.py
│   ├── Budgets_Insights.py
│   ├── Analytics.py
│   ├── Recurring_Calendar.py
│   └── Reports.py
├── tests/
├── data/                           # Local SQLite database and backups; ignored
├── .gitignore
├── app.py
├── requirements.txt
└── README.md
```

The local `data/` folder, virtual environment, Python caches, and
`.streamlit/secrets.toml` are not source files and must not be published.

## Windows installation

Open PowerShell and change to the project root:

```powershell
Set-Location C:\path\to\Personal_Expense_Tracker
```

Create and activate a virtual environment using a supported Python version
(3.12 is the Community Cloud default; replace `-3.12` with your installed
version, such as `-3.11`):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks script activation, skip activation and call the virtual
environment's Python executable directly as shown below.

Install the project's dependencies:

```powershell
python -m pip install -r requirements.txt
```

If you skipped activation, use the virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The dependency constraints are kept in the root
[requirements.txt](requirements.txt), which is also the dependency file used by
Streamlit Community Cloud.

## Authentication setup

On a new local copy with no configured password hash, the app displays an
initial setup form. Create a password of at least 12 characters. The app stores
only a salted scrypt hash in `.streamlit/secrets.toml`; that local secrets file
is ignored by Git.

The same password hash can be generated locally for a hosted secret:

```powershell
.\.venv\Scripts\python.exe -c "from getpass import getpass; from expense_tracker.auth import hash_password; print(hash_password(getpass('Password: ')))"
```

Keep the generated hash private and configure it through the hosting platform's
secrets interface. Do not put a plaintext password or hash in source code,
README files, `requirements.txt`, or committed configuration.

## Database initialization and application startup

Run Streamlit from the project root:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

The explicit address keeps this local command bound to loopback. The app
initializes the database and applies its existing migrations when an
authenticated page is first opened. The database path is anchored to the
project directory at `data/personal_finance.db`; no separate initialization
command is required.

## Automated tests

Run the complete test suite from the project root:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The database tests use temporary databases. The live local database is not
test data.

## Database and backups

Amounts are stored as integer paisa to avoid floating-point rounding. The
SQLite database is local and is not encrypted by the application.

Create a timestamped backup:

```powershell
.\.venv\Scripts\python.exe -c "from expense_tracker.database import create_database_backup; print(create_database_backup())"
```

Stop the app before restoring a backup:

```powershell
.\.venv\Scripts\python.exe -c "from expense_tracker.database import restore_database_backup; print(restore_database_backup(r'data\backups\BACKUP_FILENAME.db'))"
```

Restore validates the backup and creates a safety backup of the current
database before replacing it. Keep independent, protected copies of backups;
the local `data/backups/` folder is ignored by Git. See
[docs/DATABASE_BACKUP.md](docs/DATABASE_BACKUP.md) for additional details.

## GitHub and Streamlit Community Cloud

The repository layout is compatible with Community Cloud's root-entrypoint
workflow: `app.py`, `requirements.txt`, and `.streamlit/config.toml` are at the
repository root. Before publishing, verify that Git stages source and tests
only. Never add `data/personal_finance.db`, files under `data/backups/`, or
`.streamlit/secrets.toml`.

To prepare a Community Cloud demo:

1. Publish the source to a GitHub repository without local database, backup,
   or secret files.
2. In Community Cloud, create an app from that repository and select root
   `app.py` as the entry point.
3. Select a supported Python version matching the one used for testing (the
   current Community Cloud default is documented as Python 3.12).
4. In the app's **Secrets** settings, configure the hash generated above:

   ```toml
   [auth]
   password_hash = "scrypt$16384$8$1$<salt_hex>$<derived_key_hex>"
   ```

5. Review the build logs and perform a deployment smoke test using only
   synthetic or non-sensitive data.

See the official [Community Cloud deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy),
[dependency guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies),
and [secrets guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).

**Important storage limitation:** Community Cloud's local filesystem is
ephemeral. The SQLite database and local backups can be lost when an app is
restarted, rebuilt, or otherwise recycled. This app has no durable cloud
storage layer. A successful deployment is suitable only as a demonstration
with non-sensitive data; do not rely on it for persistent personal financial
records. The app uses one shared password, not individual accounts, and has no
per-user authorization or explicit login rate limiting.

The checked-in Streamlit config keeps the Hybrid theme and local CORS/XSRF
settings. Its server address is left unset for hosted runtimes; the local
command above explicitly binds to loopback. Community Cloud overrides some
server settings (including `server.enableXsrfProtection` in its documented
configuration), so verify the current
[Community Cloud limitations and configuration](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)
and review startup logs before sharing a hosted URL. Local configuration and
local SQLite persistence do not establish production readiness.

## Limitations and future improvements

- Local SQLite files and backups are not encrypted by the application.
- Streamlit Community Cloud does not provide durable persistence for this
  local SQLite design.
- The authentication model is single-user/shared-password, not a
  multi-user identity system.
- Automated tests focus on business and database behavior; a manual visual
  check of all six pages is still recommended.
- Screenshots and an explicit open-source license have not been added.
- Future improvements could include UI-level tests, a documented backup
  retention process, and durable storage only if the project requirements
  change.

## Author

- Name: _[Your name]_
- GitHub: _[Your GitHub profile]_
- Portfolio: _[Your portfolio URL]_

Choose and add a license before inviting reuse or contributions.

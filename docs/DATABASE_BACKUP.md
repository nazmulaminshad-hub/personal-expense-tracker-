# Database Backup and Restore

The default database is `data/personal_finance.db`.

The database and backups are local files. They are excluded from Git and
should not be committed or uploaded as public assets. Streamlit Community
Cloud's filesystem is ephemeral; this SQLite database and its backups are not
a persistent storage solution for hosted financial records.

## Create a Backup

Run this command from the project root:

```powershell
.\.venv\Scripts\python.exe -c "from expense_tracker.database import create_database_backup; print(create_database_backup())"
```

The SQLite backup API creates a timestamped file under `data/backups/`.
Existing backup files are never overwritten.

## Restore a Backup

First stop the Streamlit application. Then run:

```powershell
.\.venv\Scripts\python.exe -c "from expense_tracker.database import restore_database_backup; print(restore_database_backup(r'data\backups\BACKUP_FILENAME.db'))"
```

Replace `BACKUP_FILENAME.db` with the backup you want to restore.

Before replacing the current database, the restore function creates a separate
safety backup in `data/backups/`. Restart the application after restoration.

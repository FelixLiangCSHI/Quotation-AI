"""Out-of-process background workers.

The reminder worker is intentionally independent of Streamlit: it can be run
by cron, by a container schedule, or by APScheduler, and it keeps all of its
state in the database.
"""

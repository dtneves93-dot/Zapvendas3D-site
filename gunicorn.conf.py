# Gunicorn reads ./gunicorn.conf.py automatically.
# Apply the lead-search patch only after the Flask application is initialized.

def post_worker_init(worker):
    import search_patch  # noqa: F401
    worker.log.info("ZapVenda search fallback patch loaded")

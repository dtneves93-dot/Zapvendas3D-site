# Gunicorn reads ./gunicorn.conf.py automatically.
# Apply search and geographic-safety patches only after Flask is initialized.

def post_worker_init(worker):
    import search_patch  # noqa: F401
    import geo_patch  # noqa: F401
    worker.log.info("ZapVenda search and geographic filters loaded")

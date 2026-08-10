# Gunicorn reads ./gunicorn.conf.py automatically.
# Apply search, geographic-safety and AI guard patches only after Flask is initialized.

def post_worker_init(worker):
    import search_patch  # noqa: F401
    import geo_patch  # noqa: F401
    import ai_guard_patch  # noqa: F401
    worker.log.info("ZapVenda search, geographic filters and AI guard loaded")

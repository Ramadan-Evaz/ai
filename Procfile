
web: PYTHONPATH=$PYTHONPATH:src gunicorn -w 2 -k uvicorn.workers.UvicornWorker --pythonpath src src.ansari.app.main_api:app

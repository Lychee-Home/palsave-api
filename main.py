"""Entrypoint: starts the palsave-api service (FastAPI app + background
backup-rotation watcher thread, wired together in api.py's lifespan)."""

import logging

import uvicorn

import config
from api import app

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=config.PORT)

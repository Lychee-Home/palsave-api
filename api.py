"""FastAPI app exposing the persisted new-pal event log. Binds to
127.0.0.1 only (see main.py) -- no auth, nothing off-host can reach it.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

import config
import state as state_module
import watcher

log = logging.getLogger("palsave_api.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(
        target=watcher.run_forever,
        args=(config.BACKUP_DIR, config.ARCHIVE_DIR, config.STATE_PATH),
        daemon=True,
    )
    thread.start()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/events/new-pals")
def get_new_pals(since: int = 0, limit: int = 20):
    since = max(0, since)
    limit = max(0, limit)
    try:
        state = state_module.load_state(config.STATE_PATH)
    except (OSError, ValueError):
        log.exception("failed to read event log")
        raise HTTPException(status_code=500, detail="event log unreadable")
    return state_module.query_events(state, since, limit)

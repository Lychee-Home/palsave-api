import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKUP_DIR = Path(os.environ["PALSAVE_API_BACKUP_DIR"])
PORT = int(os.environ.get("PALSAVE_API_PORT", "8787"))

ARCHIVE_DIR = Path("snapshots")
STATE_PATH = Path("state.json")

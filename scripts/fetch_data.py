"""Download the Olist Brazilian e-commerce dataset from Kaggle into data/raw/.

Auth: kagglehub reads the KAGGLE_API_TOKEN env var directly (new-style access
token, not the legacy username+key pair). Load it from .env before running:

    uv run --group data python scripts/fetch_data.py

Separate from load_data.py on purpose: this is the only script that touches the
network or Kaggle credentials, so the CSV -> Postgres loader stays testable
without either.
"""

import logging
import os
import sys
from pathlib import Path

from _shared import EXPECTED_FILES
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
DATASET_HANDLE = "olistbr/brazilian-ecommerce"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("fetch_data")


def already_fetched() -> bool:
    return all((RAW_DIR / name).exists() for name in EXPECTED_FILES)


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")

    force = "--force" in sys.argv
    if already_fetched() and not force:
        logger.info("All expected files already present in %s, skipping download.", RAW_DIR)
        logger.info("Pass --force to re-download.")
        return

    if not os.environ.get("KAGGLE_API_TOKEN"):
        logger.error(
            "KAGGLE_API_TOKEN is not set. Generate one at "
            "https://www.kaggle.com/settings/api (expires after 3 hours) and put it "
            "in .env."
        )
        sys.exit(1)

    import kagglehub

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s to %s ...", DATASET_HANDLE, RAW_DIR)
    path = kagglehub.dataset_download(DATASET_HANDLE, output_dir=str(RAW_DIR), force_download=force)
    logger.info("Downloaded to %s", path)

    missing = [name for name in EXPECTED_FILES if not (RAW_DIR / name).exists()]
    if missing:
        logger.error("Missing expected files after download: %s", missing)
        sys.exit(1)
    logger.info("All %d expected files present.", len(EXPECTED_FILES))


if __name__ == "__main__":
    main()

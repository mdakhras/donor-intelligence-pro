# backend/services/donor_profile_ingestor.py
import os
import csv
import uuid
import logging
import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple
from azure.cosmos import CosmosClient, PartitionKey
from utils.serializers import ensure_jsonable

# ---- Env flags ----
DONOR_PROFILE_CSV_PATH = os.getenv("DONOR_PROFILE_CSV_PATH", "data/donor_data/donor_profiles.csv")
AZURE_COSMOS_DATABASE_NAME = os.getenv("AZURE_COSMOS_DATABASE_NAME", "DonorIntelDB")
AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME = os.getenv("AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME", "DonorProfile")
# Keep partition key stable (lower-snake custom field we add)
DONOR_PROFILE_PARTITION_KEY = os.getenv("DONOR_PROFILE_PARTITION_KEY", "/donor_entity_name")

DONOR_PROFILE_ENABLE = os.getenv("DONOR_PROFILE_ENABLE", "1") not in ("0","false","False")
DONOR_PROFILE_FORCE  = os.getenv("DONOR_PROFILE_FORCE", "0") in ("1","true","True")
DONOR_PROFILE_READ_ONCE = os.getenv("DONOR_PROFILE_READ_ONCE", "1") not in ("0","false","False")
DONOR_PROFILE_MARKER_ID = "_marker:donorprofile_csv"

# ---------- Helpers ----------
def _normalize_header(name: str) -> str:
    """
    Convert arbitrary column name to Title_Case_With_Underscores.
    - Splits on any non-alphanumeric.
    - Preserves ALL-CAPS acronyms (len>=3) like USAID, UNDP.
    - Title-cases other tokens (Budget -> Budget, of -> Of).
    - Collapses multiple underscores, trims leading/trailing underscores.
    - If the result starts with a digit, prefix with '_'.
    """
    import re
    tokens = re.findall(r"[A-Za-z0-9]+", name or "")
    norm_tokens: List[str] = []
    for t in tokens:
        if len(t) >= 3 and t.isupper():   # keep acronyms
            norm_tokens.append(t)
        else:
            norm_tokens.append(t[:1].upper() + t[1:].lower())
    key = "_".join(norm_tokens)
    key = re.sub(r"_+", "_", key).strip("_")
    if key and key[0].isdigit():
        key = "_" + key
    return key or "Field"

def _dedupe_headers(headers: List[str]) -> List[str]:
    """
    Ensure unique column names after normalization by appending _2, _3, ...
    """
    seen: Dict[str, int] = {}
    out: List[str] = []
    for h in headers:
        base = h
        if base not in seen:
            seen[base] = 1
            out.append(base)
        else:
            seen[base] += 1
            out.append(f"{base}_{seen[base]}")
    return out

def _open_container(cosmos_client: CosmosClient):
    db = cosmos_client.get_database_client(AZURE_COSMOS_DATABASE_NAME)
    try:
        return db.get_container_client(AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME)
    except Exception:
        logging.info(f"[CSV] Creating container '{AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME}' with PK {DONOR_PROFILE_PARTITION_KEY}")
        return db.create_container_if_not_exists(
            id=AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME,
            partition_key=PartitionKey(path=DONOR_PROFILE_PARTITION_KEY),
            offer_throughput=400,
        )

def _get_marker(container):
    try:
        items = list(container.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": DONOR_PROFILE_MARKER_ID}],
            enable_cross_partition_query=True
        ))
        return items[0] if items else None
    except Exception:
        return None

def _write_marker(container, rows: int):
    marker = {
        "id": DONOR_PROFILE_MARKER_ID,
        # satisfy PK=/donor_entity_name; keep meta value
        "donor_entity_name": "__meta__",
        "rows": rows,
        "ingested_at": datetime.datetime.utcnow().isoformat(),
        "read_once": DONOR_PROFILE_READ_ONCE
    }
    container.upsert_item(marker)

def _read_csv_rows_with_normalized_headers(path: str) -> List[Dict[str, Any]]:
    """
    Read CSV and return rows with normalized, deduped headers at root level.
    """
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return rows

        norm_headers = [_normalize_header(h) for h in raw_headers]
        norm_headers = _dedupe_headers(norm_headers)

        for i, raw_row in enumerate(reader, start=1):
            # pad/truncate to header length
            vals = list(raw_row) + [""] * (len(norm_headers) - len(raw_row))
            vals = vals[:len(norm_headers)]
            item = {}
            for k, v in zip(norm_headers, vals):
                item[k] = v.strip() if isinstance(v, str) else v
            rows.append(item)
    return rows

def ingest_csv_to_donorprofile(cosmos_client: CosmosClient) -> Tuple[int, str]:
    """
    Ingest donor CSV into Cosmos with normalized top-level fields.
    Adds:
      - id: GUID
      - donor_entity_name: from Donor_Name / Donor_Entity_Name / Entity_Name
      - parent_entity_if_applicable: from Country (if present)
      - source: csv:<path>
    Controlled by:
      DONOR_PROFILE_ENABLE, DONOR_PROFILE_READ_ONCE, DONOR_PROFILE_FORCE, DONOR_PROFILE_CSV_PATH
    """
    if not DONOR_PROFILE_ENABLE:
        logging.info("[CSV] Ingestion disabled via DONOR_PROFILE_ENABLE=0")
        return 0, DONOR_PROFILE_CSV_PATH

    container = _open_container(cosmos_client)
    marker = _get_marker(container)
    if marker and DONOR_PROFILE_READ_ONCE and not DONOR_PROFILE_FORCE:
        logging.info("[CSV] Skip: read-once marker present.")
        return 0, DONOR_PROFILE_CSV_PATH

    abs_path = str(Path(__file__).resolve().parent.parent / DONOR_PROFILE_CSV_PATH)
    if not os.path.exists(abs_path):
        logging.warning(f"[CSV] File not found: {abs_path}")
        return 0, abs_path

    base_rows = _read_csv_rows_with_normalized_headers(abs_path)
    if not base_rows:
        logging.warning(f"[CSV] No rows read from {abs_path}")
        return 0, abs_path

    written = 0
    for idx, row in enumerate(base_rows, start=1):
        # auto GUID
        row["id"] = str(uuid.uuid4())

        # special mappings (preserve your PK contract)
        donor_name = (
            row.get("Donor_Name") or
            row.get("Donor_Entity_Name") or
            row.get("Entity_Name") or
            row.get("Name") or
            ""
        )
        row["donor_entity_name"] = donor_name  # lower-snake custom field (PK)

        country_val = (
            row.get("Country") or
            row.get("Parent_Entity_If_Applicable") or
            row.get("Region") or
            ""
        )
        row["parent_entity_if_applicable"] = country_val  # lower-snake custom field

        row["source"] = f"csv:{DONOR_PROFILE_CSV_PATH}"
        now = datetime.datetime.utcnow().isoformat()
        row["csv_row_index"] = idx
        row["created_at"] = now
        row["updated_at"] = now

        try:
            container.upsert_item(ensure_jsonable(row))
            written += 1
        except Exception as e:
            logging.error(f"[CSV] Upsert failed id={row.get('id')}: {e}")

    _write_marker(container, len(base_rows))
    return written, abs_path

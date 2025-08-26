import azure.functions as func
import logging
import os
import datetime
from azure.cosmos import CosmosClient, exceptions
from services.donor_profile_ingestor import ingest_csv_to_donorprofile, DONOR_PROFILE_CSV_PATH
from utils.serializers import ensure_jsonable
import sys
from pathlib import Path
from typing import List
from services.donor_profile_matcher import lookup_donor_profile_text
import re
DOCUMENT_CHAR_LIMIT = int(os.getenv("DOCUMENT_CHAR_LIMIT", "100000"))
# Add the 'backend' directory to the path to allow for local imports
backend_path = Path(__file__).resolve().parent
sys.path.append(str(backend_path))

from main import run_donor_intel_crew, load_app_settings
from utils.helpers import get_text_from_pdf_url

RESEARCH_MODE = os.getenv("DONORINTEL_RESEARCH_MODE", "hybrid").lower()

# Configure logging to use UTF-8
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
sys.stdout.reconfigure(encoding='utf-8')

# Function to canonicalize donor names
CANON_NUM_SUFFIX_RE = re.compile(r"\s+\d+$")


def canonicalize_donor_name(name: str | None) -> str:
    """
    Normalize the donor name for prompt consistency and anti-drift.
    - trims whitespace
    - drops trailing UI suffixes like ' 09'
    """
    if not name:
        return "Unknown Donor"
    n = name.strip()
    n = CANON_NUM_SUFFIX_RE.sub("", n)
    return n

# NEW: CSV/Cosmos config (with sensible defaults)
DONOR_PROFILE_CSV_PATH = os.getenv("DONOR_PROFILE_CSV_PATH", "data/donor_data/donor_profiles.csv")
AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME = os.getenv("AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME", "DonorProfile")
DONOR_PROFILE_PARTITION_KEY = os.getenv("DONOR_PROFILE_PARTITION_KEY", "/donor_name")
DONOR_PROFILE_ALLOW_CREATE = os.getenv("DONOR_PROFILE_ALLOW_CREATE", "1") not in ("0", "false", "False")


# --- Azure Configuration ---
AZURE_COSMOS_CONNECTION_STRING = os.getenv("AZURE_COSMOS_CONNECTION_STRING")
AZURE_COSMOS_DATABASE_NAME = os.getenv("AZURE_COSMOS_DATABASE_NAME", "DonorIntelDB")
AZURE_COSMOS_CONTAINER_NAME = os.getenv("AZURE_COSMOS_CONTAINER_NAME", "Requests")

# --- Function App Initialization ---
app = func.FunctionApp()

@app.schedule(schedule="0 */50 * * * *", arg_name="myTimer", run_on_startup=True,
              use_monitor=False)
def process_pending_requests(myTimer: func.TimerRequest) -> None:
    """
    Timer-triggered function to process pending donor intelligence requests.
    - Queries Cosmos DB for requests with status 'pending'.
    - Runs the donor intelligence crew for each request.
    - Updates the request status to 'complete' and stores the result.
    """
    utc_timestamp = datetime.datetime.utcnow().replace(
        tzinfo=datetime.timezone.utc).isoformat()

    if myTimer.past_due:
        logging.info('The timer is past due!')

    logging.info('Python timer trigger function ran at %s', utc_timestamp)

    # --- Initialize Cosmos DB Client ---
    if not all([AZURE_COSMOS_CONNECTION_STRING, AZURE_COSMOS_DATABASE_NAME, AZURE_COSMOS_CONTAINER_NAME]):
        logging.error("Cosmos DB configuration is missing. Please check environment variables.")
        return

    try:
        cosmos_client = CosmosClient.from_connection_string(AZURE_COSMOS_CONNECTION_STRING)
        database_client = cosmos_client.get_database_client(AZURE_COSMOS_DATABASE_NAME)
        container_client = database_client.get_container_client(AZURE_COSMOS_CONTAINER_NAME)
    except Exception as e:
        logging.error(f"Failed to connect to Cosmos DB: {e}")
        return

    # --- Initialize DonorProfile Container Client (if available) ---
    DONOR_PROFILE_CONTAINER_NAME = os.getenv("AZURE_COSMOS_DONOR_PROFILE_CONTAINER_NAME", "DonorProfile")
    try:
        donor_profile_container = database_client.get_container_client(DONOR_PROFILE_CONTAINER_NAME)
    except Exception as e:
        logging.error(f"Failed to open DonorProfile container '{DONOR_PROFILE_CONTAINER_NAME}': {e}")
        donor_profile_container = None

    # --- Query for Pending Requests ---
    logging.info("Querying for pending requests...")
    try:
        pending_requests = list(container_client.query_items(
            query="SELECT * FROM c WHERE c.status = 'pending'",
            enable_cross_partition_query=True
        ))
    except exceptions.CosmosResourceNotFoundError:
        logging.warning(f"Cosmos DB container '{AZURE_COSMOS_CONTAINER_NAME}' not found.")
        return
    except Exception as e:
        logging.error(f"Error querying Cosmos DB: {e}")
        return

    logging.info(f"Found {len(pending_requests)} pending requests.")

    # --- Load App Settings for default values ---
    app_settings = load_app_settings()

    # --- Process Each Request ---
    for request in pending_requests:
        request_id = request.get("id")
        logging.info(f"Processing request ID: {request_id}")
        
        # Allow an optional per-request override (if frontend adds it later)
        request_mode = (request.get("research_mode") or RESEARCH_MODE or "hybrid").lower()
        if request_mode not in ("hybrid", "docs_only", "web_only"):
            request_mode = "hybrid"
        try:
            # 1. Update status to 'processing' to prevent reprocessing
            request['status'] = 'processing'
            container_client.replace_item(item=request, body=request)
            logging.info(f"Request ID: {request_id} status updated to 'processing'.")

            
            # 2. Extract text from any provided documents (prefer pre-extracted content)
            document_content = ""
            documents_processed = 0
            doc_warnings: List[str] = []

            additional_docs = request.get("additional_documents") or []
            if additional_docs:
                logging.info(f"Extracting text from {len(additional_docs)} document(s)...")
                for doc in additional_docs:
                    url = (doc or {}).get("document_url")
                    text = (doc or {}).get("content") or ""   # ← prefer frontend-provided content

                    if not text and url:
                        # Fallback: backfill by downloading & extracting now
                        text = get_text_from_pdf_url(url, hard_limit=DOCUMENT_CHAR_LIMIT) if callable(get_text_from_pdf_url) else ""

                    if text:
                        # Cap and persist inside the doc object so it's visible in Cosmos
                        if len(text) > DOCUMENT_CHAR_LIMIT:
                            text = text[:DOCUMENT_CHAR_LIMIT] + "\n[TRUNCATED]"
                        doc["content"] = text
                        doc["content_char_count"] = len(text)
                        document_content += (text + "\n\n---\n\n")
                        documents_processed += 1
                    else:
                        if url:
                            doc_warnings.append(f"Empty/unreadable content from: {url}")
                        else:
                            doc_warnings.append("Document entry missing 'document_url'.")

            # hard cap to keep final concatenation safe
            if len(document_content) > DOCUMENT_CHAR_LIMIT:
                document_content = document_content[:DOCUMENT_CHAR_LIMIT] + "\n[TRUNCATED]" 
             
             
             # 2b. Look up existing donor profile (best fuzzy match)
            existing_profile_text = ""
            match_meta = {}
            if donor_profile_container is not None:
                try:
                    existing_profile_text, match_meta = lookup_donor_profile_text(
                        donor_profile_container,
                        request.get("donor_name")
                    )
                    if existing_profile_text:
                        logging.info(f"Matched existing donor profile: {match_meta}")
                    else:
                        logging.info("No suitable donor profile match found or below score threshold.")
                except Exception as e:
                    logging.warning(f"DonorProfile lookup failed: {e}")
                    
                    
            # 2c. Canonicalize donor name for prompts/governance
            canonical_name = canonicalize_donor_name(request.get("donor_name"))
            request["canonical_donor_name"] = canonical_name    # keep for traceability in Cosmos
            
                
            # 3. Prepare inputs for the crew
            #    - Use data from the request
            #    - Fallback to default settings for missing parameters
            crew_inputs = {
                "donor_name": request.get("donor_name"),
                "canonical_donor_name": canonical_name,
                "region": request.get("country"),
                "theme": request.get("thematic_area"),
                # "keywords": request.get("desc"), # Using desc as a source for keywords
                "user_role": app_settings.get("default_user_role", "HQ"),
                "existing_profile":  existing_profile_text or " ", 
                "recent_activity": "...",   # Placeholder
                "document_content": document_content,
                "research_mode": request_mode,   
            }

            logging.info(f"Running crew for request {request_id}")

            logging.info(f"Doc chars: {len(document_content)} | Docs processed: {documents_processed}")
            # logging.info(f"LLM input paramters: {crew_inputs}")


            # 4. Run the crew
            crew_result = run_donor_intel_crew(**crew_inputs)


            # payload = {
            #         # ... all your other fields ...
            #         "crew_result": ensure_jsonable(crew_result),   # <— critical line
            #     }
            
            
            # 5. Update request with result and set status to 'complete'
            request['status'] = 'complete'
            request['completed_at'] = datetime.datetime.utcnow().isoformat()
            request['crew_result'] = ensure_jsonable(crew_result)

            # NEW: persist document ingestion details for observability
            request['document_content'] = document_content               # ← now visible in Cosmos
            request['document_char_count'] = len(document_content)
            request['documents_processed'] = documents_processed
            request['research_mode'] = request_mode
            if doc_warnings:
                request['document_warnings'] = doc_warnings
                
            if match_meta:
                request['matched_donor_profile'] = match_meta

            if not additional_docs:
                request['notes'] = "No documents were provided for processing."
            elif documents_processed == 0:
                request['notes'] = "Documents were provided but text could not be extracted."
                
                
            container_client.replace_item(item=request, body=request)
            logging.info(f"Request ID: {request_id} processed successfully and status updated to 'complete'.")

        except Exception as e:
            logging.error(f"Failed to process request ID: {request_id}. Error: {e}")
            # Optionally, update status to 'failed' to handle errors
            try:
                request['status'] = 'failed'
                request['error_message'] = str(e)
                container_client.replace_item(item=request, body=request)
            except Exception as ie:
                logging.error(f"Failed to update status to 'failed' for request ID: {request_id}. Error: {ie}")

    logging.info("Finished processing requests.")



# function_app.py
@app.schedule(schedule="0 */50 * * * *", arg_name="csvTimer", run_on_startup=False, use_monitor=False)
def ingest_donor_profiles_csv(csvTimer: func.TimerRequest) -> None:
    if not AZURE_COSMOS_CONNECTION_STRING:
        logging.error("[CSV] Cosmos connection string missing.")
        return
    cosmos_client = CosmosClient.from_connection_string(AZURE_COSMOS_CONNECTION_STRING)
    count, path = ingest_csv_to_donorprofile(cosmos_client)
    logging.info(f"[CSV] Upserted {count} row(s) from {path}")
import os
import uuid
import datetime
from dotenv import load_dotenv
import streamlit as st
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.cosmos import CosmosClient
import io
from pypdf import PdfReader  # make sure pypdf is in requirements.txt

DOCUMENT_CHAR_LIMIT = int(os.getenv("DOCUMENT_CHAR_LIMIT", "100000"))


def extract_pdf_text_from_bytes(data: bytes, hard_limit: int = DOCUMENT_CHAR_LIMIT) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for p in reader.pages:
            parts.append(p.extract_text() or "")
        text = "\n".join(parts).strip()
        if len(text) > hard_limit:
            text = text[:hard_limit] + "\n[TRUNCATED]"
        return text
    except Exception:
        # keep UI responsive; just return empty if extraction fails
        return ""

# --- Env & settings ---
load_dotenv()

# --- Azure Configuration ---
# It's recommended to use environment variables for connection strings and keys
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "pdf-uploads")
AZURE_COSMOS_CONNECTION_STRING = os.getenv("AZURE_COSMOS_CONNECTION_STRING")
AZURE_COSMOS_DATABASE_NAME = os.getenv("AZURE_COSMOS_DATABASE_NAME", "DonorIntelDB")
AZURE_COSMOS_CONTAINER_NAME = os.getenv("AZURE_COSMOS_CONTAINER_NAME", "Requests")

# --- Helper Functions ---
def get_blob_service_client():
    """Initializes and returns a BlobServiceClient."""
    if not AZURE_STORAGE_CONNECTION_STRING:
        st.error("Azure Storage connection string is not configured.")
        return None
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)

def get_cosmos_container_client():
    """Initializes and returns a Cosmos DB container client."""
    if not AZURE_COSMOS_CONNECTION_STRING:
        st.error("Azure Cosmos DB connection string is not configured.")
        return None
    try:
        client = CosmosClient.from_connection_string(AZURE_COSMOS_CONNECTION_STRING)
        database = client.get_database_client(AZURE_COSMOS_DATABASE_NAME)
        container = database.get_container_client(AZURE_COSMOS_CONTAINER_NAME)
        return container
    except Exception as e:
        st.error(f"Failed to connect to Cosmos DB: {e}")
        return None

def upload_files_to_blob_storage(files, request_id):
    blob_service_client = get_blob_service_client()
    if not blob_service_client:
        return None

    document_details = []

    progress_bar = st.progress(0)
    total_files = len(files)
    upload_placeholder = st.empty()

    for i, uploaded_file in enumerate(files):
        blob_name = f"{request_id}/{uploaded_file.name}"
        blob_client = blob_service_client.get_blob_client(
            container=AZURE_STORAGE_CONTAINER_NAME, blob=blob_name
        )

        upload_placeholder.info(f"Uploading {uploaded_file.name} ({i+1}/{total_files})...")

        # Read file bytes once; use it for both upload and text extraction
        file_bytes = uploaded_file.getvalue()

        # 1) upload
        try:
            blob_client.upload_blob(file_bytes, overwrite=True, timeout=300)
        except Exception as e:
            st.error(f"Failed to upload {uploaded_file.name}. Error: {e}")
            st.stop()

        # 2) generate SAS
        sas_token = generate_blob_sas(
            account_name=blob_service_client.account_name,
            container_name=AZURE_STORAGE_CONTAINER_NAME,
            blob_name=blob_name,
            account_key=blob_service_client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        )
        url_with_sas = f"{blob_client.url}?{sas_token}"

        # 3) extract text and include it in the document object
        extracted_text = extract_pdf_text_from_bytes(file_bytes, hard_limit=DOCUMENT_CHAR_LIMIT)

        document_details.append({
            "document_name": uploaded_file.name,
            "document_url": url_with_sas,
            "content": extracted_text,                               # ← NEW
            "content_char_count": len(extracted_text or ""),          # ← helpful meta
        })

        progress_bar.progress((i + 1) / total_files)

    upload_placeholder.empty()
    return document_details


# --- Streamlit page config ---
st.set_page_config(page_title="Donor Intelligence Assistant", layout="wide")
st.title("🤖 Donor Intelligence Assistant")
st.subheader("Submit a new donor intelligence request")

# --- Form ---
with st.form("input_form", clear_on_submit=True):
    donor_name = st.text_input("Donor Name", key="donor_name", placeholder="e.g., Bill & Melinda Gates Foundation")
    thematic_area = st.text_input("Thematic Area", key="thematic_area", placeholder="e.g., Global Health")
    country = st.text_input("Country / Region", key="country", placeholder="e.g., Sub-Saharan Africa")
    desc = st.text_area("Description", key="desc", placeholder="Provide a brief description of the request...")

    additional_documents = st.file_uploader(
        "Upload Additional Documents",
        type="pdf",
        accept_multiple_files=True,
        key="additional_documents"
    )

    submitted = st.form_submit_button("Submit Request")

if submitted:
    if not all([donor_name, thematic_area, country, desc]):
        st.warning("Please fill out all required fields.")
    else:
        st.info("Submitting your request... Please wait.")

        request_id = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"

        # 1. Upload files to Blob Storage
        document_details = []
        if additional_documents:
            uploaded_docs = upload_files_to_blob_storage(additional_documents, request_id)
            if uploaded_docs is not None:
                document_details = uploaded_docs
            else:
                st.error("Failed to upload documents. Please check storage configuration.")
                st.stop() # Halt execution if upload fails

        # 2. Store metadata in Cosmos DB
        cosmos_container_client = get_cosmos_container_client()
        if cosmos_container_client:
            request_data = {
                "id": request_id,
                "donor_name": donor_name,
                "thematic_area": thematic_area,
                "country": country,
                "desc": desc,
                "additional_documents": document_details,
                "status": "pending",
                "submitted_at": datetime.datetime.utcnow().isoformat()
            }

            try:
                cosmos_container_client.create_item(body=request_data)
                st.success(f"✅ Request submitted successfully! Your Request ID is: **{request_id}**")
            except Exception as e:
                st.error(f"Failed to save request to database. Error: {e}")
        else:
            st.error("Failed to submit request. Please check database configuration.")

# --- Display connection status for debugging ---
with st.sidebar:
    st.header("Azure Connection Status")
    if os.getenv("AZURE_STORAGE_CONNECTION_STRING"):
        st.success("Storage Connection: Configured")
    else:
        st.warning("Storage Connection: Not Configured")

    if os.getenv("AZURE_COSMOS_CONNECTION_STRING"):
        st.success("Cosmos DB Connection: Configured")
    else:
        st.warning("Cosmos DB Connection: Not Configured")

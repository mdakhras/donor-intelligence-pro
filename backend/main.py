import os
from pathlib import Path
import json
from dotenv import load_dotenv
from typing import Dict, Any
import logging
# CrewAI / LangChain
from crewai import Agent, Task
from langchain_openai import AzureChatOpenAI
from crewai import LLM
import yaml
from utils.helpers import load_prompt
from utils.web_scraper import search_donor_articles, RAW_INPUTS_DIR
from models import ResearchData, DonorProfile, Strategy, Guidance, ReportDraft, FinalReport
from crew import DonorCrew
import re
load_dotenv()



def load_app_settings() -> Dict[str, Any]:
    with open("config/app_settings.json", "r", encoding="utf-8") as f:
        return json.load(f)




def _require_env(var: str) -> str:
    val = os.getenv(var)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return val


def _build_llm() -> AzureChatOpenAI:
    """Create a LangChain-compatible Azure OpenAI chat model for CrewAI agents."""
    # Load secrets from .env
    load_dotenv()

     
    
    # Validate required Azure OpenAI settings
    azure_endpoint = _require_env("OPENAI_API_BASE")
    api_key = _require_env("OPENAIAPI_KEY")
    api_version = _require_env("OPENAI_API_VERSION")
    deployment_name = _require_env("OPENAI_MODEL")  # This must be the Azure *deployment name*
    
    
   
    
    
    # print("OPENAI_API_BASE:", os.getenv("OPENAI_API_BASE"))
    # print("OPENAI_API_KEY:", os.getenv("OPENAIAPI_KEY"))
    # print("OPENAI_API_VERSION:", os.getenv("OPENAI_API_VERSION"))
    # print("OPENAI_MODEL:", os.getenv("OPENAI_MODEL"))
  
    logging.debug("llm initialized....") 
   
    
    llm = LLM(
        model=f"azure/{os.getenv('OPENAI_MODEL')}",
        api_key=os.getenv("OPENAIAPI_KEY"),
        api_base=os.getenv("OPENAI_API_BASE"),
        api_version=os.getenv("OPENAI_API_VERSION"),
        temperature=0
        )

     # Preflight: fail fast if the deployment isn’t reachable
    _ping_azure_openai_or_raise()
    return llm


def _ping_azure_openai_or_raise():
    """Fail fast with a clear error message if Azure OpenAI wiring is wrong."""
    # Initialize the LLM
    client = AzureChatOpenAI(
        azure_deployment=os.getenv("OPENAI_MODEL"),
        openai_api_version=os.getenv("OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("OPENAI_API_BASE"),
        api_key=os.getenv("OPENAIAPI_KEY"),
        temperature=0.3,
        max_retries=3,
    )

    # IMPORTANT: model must be your Azure deployment name
    model = os.getenv("OPENAI_MODEL")
    try:
        # Use the AzureChatOpenAI instance directly to send messages
        response = client.invoke([
        ("system", "You are a concise assistant!"),
        ("human", "Ping!")
    ])
        print("Preflight check successful:", response)
    except Exception as e:
        raise RuntimeError(
            "Azure OpenAI preflight failed.\n"
            f"- OPENAI_API_BASE={os.getenv('OPENAI_API_BASE')}\n"
            f"- OPENAI_API_VERSION={os.getenv('OPENAI_API_VERSION')}\n"
            f"- OPENAI_MODEL(deployment)={model}\n\n"
            "Verify that OPENAI_MODEL matches the Azure *deployment name*,\n"
            "the endpoint is your resource .openai.azure.com, and the API version is valid\n"
            f"Original error: {e}"
        )
        


def run_donor_intel_crew(
    donor_name, canonical_donor_name, region, theme, user_role,
    existing_profile, recent_activity, document_content: str = "",
    research_mode: str = "hybrid",
):
    research_mode = (research_mode or "hybrid").lower()
    if research_mode not in ("hybrid", "docs_only", "web_only"):
        research_mode = "hybrid"

    app_settings = load_app_settings()
    # with open("config/crew_config.yaml", "r", encoding="utf-8") as f:
    #     config = yaml.safe_load(f)

    scraped_results = []
    if research_mode in ("hybrid", "web_only"):
        max_results = int(app_settings.get("max_web_results", 5))
        save_flag = bool(app_settings.get("cache_scraper_results", True))
        scraped_results = search_donor_articles(
            donor_name=donor_name, region=region, theme=theme,
            max_results=max_results, save_to_disk=save_flag,
        ) or []

    notes_parts = []
    if research_mode in ("hybrid", "docs_only") and document_content:
        notes_parts.append("### DOCUMENT EXCERPTS\n" + document_content.strip())
    if research_mode in ("hybrid", "web_only") and scraped_results:
        joined = "\n\n---\n\n".join([f"{r.get('title','')}\n{r.get('body','')}\n{r.get('url','')}" for r in scraped_results])
        notes_parts.append("### WEB EXCERPTS\n" + joined)
    research_notes = "\n\n".join(notes_parts).strip()

    if not research_notes:
        logging.warning(f"Insufficient evidence for '{donor_name}'. No document content or web results found.")
        return {
            "final_report": f"Insufficient evidence for {donor_name}. No usable document content or web results were found.",
            "_debug": {"research_mode": research_mode, "scraped_items_count": len(scraped_results), "document_present": bool(document_content)}
        }

    llm = _build_llm()
   
   
    donor_crew = DonorCrew()
    crew_instance = donor_crew.generate_donor_crew()
    
    section_input = {
        "donor_name": donor_name,
        "region": region,
        "theme": theme,
        "scrapped_data": research_notes,
        "document_content": document_content,
        "research_mode": research_mode,
        "canonical_donor_name": canonical_donor_name,
        "existing_profile": existing_profile,
        "recent_activity": recent_activity,
        "user_role": user_role,
        
    }

    result = crew_instance.kickoff(inputs=section_input)
    raw_output = result.raw.replace("`", "")
    raw_output = re.sub(r'[\x00-\x1F\x7F]', '', raw_output)  
    # parsed = json.loads(raw_output)
    return result
    
   
   #CHAINING THE TASKS TOGETHER
    # agents: Dict[str, Agent] = {}
    # for agent_cfg in config["agents"]:
    #     prompt_template = load_prompt(agent_cfg["prompt_path"])
    #     agents[agent_cfg["name"]] = Agent(
    #         name=agent_cfg["name"], role=agent_cfg["role"], goal=agent_cfg["goal"],
    #         backstory=agent_cfg.get("backstory", ""), prompt_template=prompt_template, llm=llm,
    #     )
        
    
    
    # # Define and execute tasks sequentially
    # research_inputs = {"donor_name": donor_name, "region": region, "theme": theme, "scrapped_data": research_notes, "document_content": document_content, "research_mode": research_mode, "canonical_donor_name": canonical_donor_name}
    # research_task = Task(
    #     description="Collect online insights about the donor",
    #     agent=agents['DonorResearchAgent'],
    #     expected_output="A JSON object containing a list of research bullet points with source URLs.",
    #     output_pydantic=ResearchData,
    #     inputs=research_inputs
    # )
    # research_data = research_task.execute_sync()

    # synthesize_inputs = {"donor_name": donor_name, "region": region, "theme": theme, "research_data": research_data.model_dump(), "existing_profile": existing_profile, "document_content": document_content, "research_mode": research_mode, "canonical_donor_name": canonical_donor_name}
    # synthesize_task = Task(
    #     description="Build a profile from research and previous donor records",
    #     agent=agents['ProfileSynthesizerAgent'],
    #     expected_output="A JSON object representing a structured donor profile with sections for key players, priorities, and funding history.",
    #     output_pydantic=DonorProfile,
    #     inputs=synthesize_inputs
    # )
    # donor_profile = synthesize_task.execute_sync()

    # strategy_inputs = {"donor_name": donor_name, "region": region, "theme": theme, "donor_profile": donor_profile.model_dump(), "recent_activity": recent_activity}
    # strategy_task = Task(
    #     description="Suggest engagement strategy based on donor profile",
    #     agent=agents['StrategyRecommenderAgent'],
    #     expected_output="A JSON object containing a strategic recommendation and justification.",
    #     output_pydantic=Strategy,
    #     inputs=strategy_inputs
    # )
    # strategy = strategy_task.execute_sync()

    # guidance_inputs = {"donor_name": donor_name}
    # guidance_task = Task(
    #     description="Provide standard outreach instructions based on donor type",
    #     agent=agents['GuidanceAgent'],
    #     expected_output="A JSON object with engagement instructions, funding cycle info, and standard advisory notes.",
    #     output_pydantic=Guidance,
    #     inputs=guidance_inputs
    # )
    # guidance = guidance_task.execute_sync()

    # report_inputs = {"donor_profile": donor_profile.model_dump(), "strategy": strategy.model_dump(), "guidance": guidance.model_dump()}
    # report_task = Task(
    #     description="Produce a fundraising report tailored to audience",
    #     agent=agents['ReportWriterAgent'],
    #     expected_output="A JSON object containing the editable donor intelligence report text.",
    #     output_pydantic=ReportDraft,
    #     inputs=report_inputs
    # )
    # report_draft = report_task.execute_sync()

    # governance_inputs = {"report_draft": report_draft.model_dump(), "user_role": user_role}
    # governance_task = Task(
    #     description="Redact sensitive information based on user role",
    #     agent=agents['GovernanceAgent'],
    #     expected_output="A JSON object containing the finalized, redacted donor report.",
    #     output_pydantic=FinalReport,
    #     inputs=governance_inputs
    # )
    # final_report = governance_task.execute_sync()

    # result = {"final_report": final_report.report}

    # if app_settings.get("enable_debug_logging", True):
    #     latest_file = None
    #     try:
    #         candidates = sorted(Path(RAW_INPUTS_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    #         latest_file = str(candidates[0]) if candidates else None
    #     except Exception:
    #         latest_file = None
    #     result.setdefault("_debug", {})
    #     result["_debug"].update({"raw_inputs_saved": save_flag, "raw_inputs_latest_file": latest_file})

    # return result



#     from fastapi import FastAPI, File, UploadFile, Form, HTTPException
#     from fastapi.middleware.cors import CORSMiddleware
#     from dotenv import load_dotenv
#     from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
#     from azure.cosmos import CosmosClient
#     from pypdf import PdfReader

#     # Load env variables
#     load_dotenv()

#     DOCUMENT_CHAR_LIMIT = int(os.getenv("DOCUMENT_CHAR_LIMIT", "100000"))

#     AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
#     AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "pdf-uploads")
#     AZURE_COSMOS_CONNECTION_STRING = os.getenv("AZURE_COSMOS_CONNECTION_STRING")
#     AZURE_COSMOS_DATABASE_NAME = os.getenv("AZURE_COSMOS_DATABASE_NAME", "DonorIntelDB")
#     AZURE_COSMOS_CONTAINER_NAME = os.getenv("AZURE_COSMOS_CONTAINER_NAME", "Requests")

#     app = FastAPI()

#     # Allow React Native app to call API
#     app.add_middleware(
#         CORSMiddleware,
#         allow_origins=["*"],  # in production restrict to your domain
#         allow_credentials=True,
#         allow_methods=["*"],
#         allow_headers=["*"],
#     )

#     # ---------- Helpers ----------
#     def extract_pdf_text_from_bytes(data: bytes, hard_limit: int = DOCUMENT_CHAR_LIMIT) -> str:
#         try:
#             reader = PdfReader(io.BytesIO(data))
#             parts = [(p.extract_text() or "") for p in reader.pages]
#             text = "\n".join(parts).strip()
#             if len(text) > hard_limit:
#                 text = text[:hard_limit] + "\n[TRUNCATED]"
#             return text
#         except Exception:
#             return ""


#     def get_blob_service_client():
#         if not AZURE_STORAGE_CONNECTION_STRING:
#             raise RuntimeError("Azure Storage connection string is not configured.")
#         return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


#     def get_cosmos_container_client():
#         if not AZURE_COSMOS_CONNECTION_STRING:
#             raise RuntimeError("Azure Cosmos DB connection string is not configured.")
#         try:
#             client = CosmosClient.from_connection_string(AZURE_COSMOS_CONNECTION_STRING)
#             database = client.get_database_client(AZURE_COSMOS_DATABASE_NAME)
#             return database.get_container_client(AZURE_COSMOS_CONTAINER_NAME)
#         except Exception as e:
#             raise RuntimeError(f"Failed to connect to Cosmos DB: {e}")

# # ---------- API Endpoint ----------
#     @app.post("/submit-request")
#     async def submit_request(
#         donor_name: str = Form(...),
#         thematic_area: str = Form(...),
#         country: str = Form(...),
#         desc: str = Form(...),
#         files: List[UploadFile] = File(None),
#     ):
#         try:
#             request_id = f"{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"

#             document_details = []
#             blob_service_client = get_blob_service_client()

#             # Handle file uploads
#             if files:
#                 for file in files:
#                     blob_name = f"{request_id}/{file.filename}"
#                     blob_client = blob_service_client.get_blob_client(
#                         container=AZURE_STORAGE_CONTAINER_NAME, blob=blob_name
#                     )

#                     file_bytes = await file.read()

#                     # Upload
#                     blob_client.upload_blob(file_bytes, overwrite=True, timeout=300)

#                     # SAS URL
#                     sas_token = generate_blob_sas(
#                         account_name=blob_service_client.account_name,
#                         container_name=AZURE_STORAGE_CONTAINER_NAME,
#                         blob_name=blob_name,
#                         account_key=blob_service_client.credential.account_key,
#                         permission=BlobSasPermissions(read=True),
#                         expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=24),
#                     )
#                     url_with_sas = f"{blob_client.url}?{sas_token}"

#                     # Extract PDF text
#                     extracted_text = extract_pdf_text_from_bytes(file_bytes)

#                     document_details.append({
#                         "document_name": file.filename,
#                         "document_url": url_with_sas,
#                         "content": extracted_text,
#                         "content_char_count": len(extracted_text or ""),
#                     })

#             # Store metadata in Cosmos DB
#             cosmos_container_client = get_cosmos_container_client()
#             request_data = {
#                 "id": request_id,
#                 "donor_name": donor_name,
#                 "thematic_area": thematic_area,
#                 "country": country,
#                 "desc": desc,
#                 "additional_documents": document_details,
#                 "status": "pending",
#                 "submitted_at": datetime.datetime.utcnow().isoformat()
#             }

#             cosmos_container_client.create_item(body=request_data)

#             return {"success": True, "request_id": request_id}

#         except Exception as e:
#             raise HTTPException(status_code=500, detail=str(e))
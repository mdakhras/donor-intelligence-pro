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
    with open("config/crew_config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

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
    agents: Dict[str, Agent] = {}
    for agent_cfg in config["agents"]:
        prompt_template = load_prompt(agent_cfg["prompt_path"])
        agents[agent_cfg["name"]] = Agent(
            name=agent_cfg["name"], role=agent_cfg["role"], goal=agent_cfg["goal"],
            backstory=agent_cfg.get("backstory", ""), prompt_template=prompt_template, llm=llm,
        )

    # Define tasks with Pydantic output models
    research_task = Task(
        description="Collect online insights about the donor",
        agent=agents['DonorResearchAgent'],
        expected_output="A JSON object containing a list of research bullet points with source URLs.",
        output_pydantic=ResearchData
    )
    synthesize_task = Task(
        description="Build a profile from research and previous donor records",
        agent=agents['ProfileSynthesizerAgent'],
        expected_output="A JSON object representing a structured donor profile with sections for key players, priorities, and funding history.",
        output_pydantic=DonorProfile
    )
    strategy_task = Task(
        description="Suggest engagement strategy based on donor profile",
        agent=agents['StrategyRecommenderAgent'],
        expected_output="A JSON object containing a strategic recommendation and justification.",
        output_pydantic=Strategy
    )
    guidance_task = Task(
        description="Provide standard outreach instructions based on donor type",
        agent=agents['GuidanceAgent'],
        expected_output="A JSON object with engagement instructions, funding cycle info, and standard advisory notes.",
        output_pydantic=Guidance
    )
    report_task = Task(
        description="Produce a fundraising report tailored to audience",
        agent=agents['ReportWriterAgent'],
        expected_output="A JSON object containing the editable donor intelligence report text.",
        output_pydantic=ReportDraft
    )
    governance_task = Task(
        description="Redact sensitive information based on user role",
        agent=agents['GovernanceAgent'],
        expected_output="A JSON object containing the finalized, redacted donor report.",
        output_pydantic=FinalReport
    )

    # Execute tasks sequentially
    research_data = research_task.execute_sync(inputs={"donor_name": donor_name, "region": region, "theme": theme, "scrapped_data": research_notes, "document_content": document_content, "research_mode": research_mode, "canonical_donor_name": canonical_donor_name})
    donor_profile = synthesize_task.execute_sync(inputs={"donor_name": donor_name, "region": region, "theme": theme, "research_data": research_data.model_dump_json(), "existing_profile": existing_profile, "document_content": document_content, "research_mode": research_mode, "canonical_donor_name": canonical_donor_name})
    strategy = strategy_task.execute_sync(inputs={"donor_name": donor_name, "region": region, "theme": theme, "donor_profile": donor_profile.model_dump_json(), "recent_activity": recent_activity})
    guidance = guidance_task.execute_sync(inputs={"donor_name": donor_name})
    report_draft = report_task.execute_sync(inputs={"donor_profile": donor_profile.model_dump_json(), "strategy": strategy.model_dump_json(), "guidance": guidance.model_dump_json()})
    final_report = governance_task.execute_sync(inputs={"report_draft": report_draft.model_dump_json(), "user_role": user_role})

    result = {"final_report": final_report.report}

    if app_settings.get("enable_debug_logging", True):
        latest_file = None
        try:
            candidates = sorted(Path(RAW_INPUTS_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            latest_file = str(candidates[0]) if candidates else None
        except Exception:
            latest_file = None
        result.setdefault("_debug", {})
        result["_debug"].update({"raw_inputs_saved": save_flag, "raw_inputs_latest_file": latest_file})

    return result


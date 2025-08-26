import os
from pathlib import Path
import json
from dotenv import load_dotenv
from typing import Dict, Any
import logging
# CrewAI / LangChain
from crewai import Crew, Agent, Task, Process
from langchain_openai import AzureChatOpenAI
from crewai import LLM
import yaml
from utils.helpers import load_prompt, render_prompt
from utils.web_scraper import search_donor_articles, RAW_INPUTS_DIR
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
    # from crewai import Crew, Agent, Task
    # import yaml
    # from utils.helpers import load_prompt
    # from dotenv import load_dotenv
    # # from langchain.chat_models import AzureChatOpenAI
    # from langchain_openai import AzureChatOpenAI
    # # from openai import AzureOpenAI
    # import os
    # load_dotenv()
    
    research_mode = (research_mode or "hybrid").lower()
    if research_mode not in ("hybrid", "docs_only", "web_only"):
        research_mode = "hybrid"

    # Load Crew configuration and app settings
    app_settings = load_app_settings()
    with open("config/crew_config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 1) Collect sources according to research mode
    scraped_results = []
    if research_mode in ("hybrid", "web_only"):
        max_results = int(app_settings.get("max_web_results", 5))
        save_flag = bool(app_settings.get("cache_scraper_results", True))
        scraped_results = search_donor_articles(
            donor_name=donor_name,
            region=region,
            theme=theme,
            max_results=max_results,
            save_to_disk=save_flag,
        ) or []

    # 2) Build research notes from collected sources
    notes_parts = []
    if research_mode in ("hybrid", "docs_only") and document_content:
        notes_parts.append("### DOCUMENT EXCERPTS\n" + document_content.strip())

    if research_mode in ("hybrid", "web_only") and scraped_results:
        joined = "\n\n---\n\n".join([f"{r.get('title','')}\n{r.get('body','')}\n{r.get('url','')}" for r in scraped_results])
        notes_parts.append("### WEB EXCERPTS\n" + joined)

    research_notes = "\n\n".join(notes_parts).strip()

    # 3) If there's no content to process, exit early
    if not research_notes:
        logging.warning(f"Insufficient evidence for '{donor_name}'. No document content or web results found.")
        return {
            "final_report": f"Insufficient evidence for {donor_name}. No usable document content or web results were found.",
            "_debug": {
                "research_mode": research_mode,
                "scraped_items_count": len(scraped_results),
                "document_present": bool(document_content),
            }
        }

    # 4) Build the LLM once and share across agents
    llm = _build_llm()

    # 5) Initialize agents from config and attach the LLM
    agents: Dict[str, Agent] = {}
    for agent_cfg in config["agents"]:
        prompt_template = load_prompt(agent_cfg["prompt_path"])
        agent = Agent(
            name=agent_cfg["name"],
            role=agent_cfg["role"],
            goal=agent_cfg["goal"],
            backstory=agent_cfg.get("backstory", ""),
            prompt_template=prompt_template,
            llm=llm,
        )
        agents[agent_cfg["name"]] = agent

    
    

    # 5) Runtime inputs (also passed to tasks where needed)
    runtime_inputs = {
        "donor_name": donor_name,
        "canonical_donor_name": canonical_donor_name,
        "region": region,
        "theme": theme,
        "user_role": user_role,
        "existing_profile": existing_profile,
        "recent_activity": recent_activity,
        # NEW: make both raw list and formatted text available
        "scrapped_data": research_notes, #scraped,
        # "research_snippets": research_notes,
        "document_content": document_content,
        "research_mode": research_mode,
        
    }


    # print("Inside runtime fields:", runtime_inputs)
    # 6) Build tasks according to routing
    tasks = []
    for task_cfg in config["task_routing"]:
        # Collect declared inputs for this task: pull from previous outputs at runtime, or runtime_inputs
        declared_inputs: Dict[str, Any] = {}
        for key in task_cfg.get("inputs", []):
            if key in runtime_inputs:
                declared_inputs[key] = runtime_inputs[key]
        task = Task(
            description=task_cfg["description"],
            agent=agents[task_cfg["assigned_to"]],
            expected_output=task_cfg.get("expected_output", None),
            inputs=declared_inputs,
            output_key=task_cfg.get("output_key"),
        )
        # logging.info(f"Mo Task {task.description} -> output_key={task.output_key}, inputs={list(task.inputs.keys())}")
        tasks.append(task)

    # 7) Create Crew and kickoff (newer CrewAI versions)
    crew = Crew(
        name=config["crew_name"],
        description=config["description"],
        tasks=tasks,
        process=Process.sequential,
        verbose=False, #app_settings.get("enable_debug_logging", True),
    )

    result = crew.kickoff()

    # 8) Attach debug info for UI (optional)
    if app_settings.get("enable_debug_logging", True):
        # Grab newest raw_inputs file matching donor/region/theme
        latest_file = None
        try:
            candidates = sorted(Path(RAW_INPUTS_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            latest_file = str(candidates[0]) if candidates else None
        except Exception:
            latest_file = None
        # Return dict so Streamlit can inspect it
        if isinstance(result, dict):
            result.setdefault("_debug", {})
            result["_debug"].update({
                "raw_inputs_saved": save_flag,
                "raw_inputs_latest_file": latest_file,
                # "scraped_items_count": len(scraped),
            })
        else:
            result = {
                "final_report": str(result),
                "_debug": {
                    "raw_inputs_saved": save_flag,
                    "raw_inputs_latest_file": latest_file,
                    # "scraped_items_count": len(scraped),
                },
            }

    return result


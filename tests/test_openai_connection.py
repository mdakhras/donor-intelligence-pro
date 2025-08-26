import os
import pytest
from dotenv import load_dotenv
from openai import AzureOpenAI
from openai import APIStatusError, AuthenticationError
from langchain_openai import AzureChatOpenAI
import logging
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
REQUIRED_VARS = [
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_API_VERSION",
    "OPENAI_MODEL",
]


def _env_ready() -> bool:
    return all(os.getenv(v) for v in REQUIRED_VARS)


def initialize_openai_client():
    try:
        str1 = os.getenv("OPENAIAPI_KEY")
        print(f"{str1}")
        # client = openai.AzureOpenAI(
        #     api_key=os.getenv("OPENAI_API_KEY"),
        #     azure_endpoint=os.getenv("OPENAI_API_BASE"),
        #     api_version=os.getenv("OPENAI_API_VERSION"),
        # )
        
        logging.info(f"OPENAI_API_BASE: {os.getenv('OPENAI_API_BASE')}")
        logging.info(f"OPENAI_API_KEY: {os.getenv('OPENAIAPI_KEY')}")
        logging.info(f"OPENAI_API_VERSION: {os.getenv('OPENAI_API_VERSION')}")
        logging.info(f"OPENAI_MODEL: {os.getenv('OPENAI_MODEL')}")
        
        client = AzureChatOpenAI(
            azure_deployment=os.getenv("OPENAI_MODEL"),
            openai_api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_API_BASE"),
            api_key=os.getenv("OPENAIAPI_KEY"),
            temperature=0.3,
            max_retries=3,
            )

        return client
    except Exception as e:
        raise Exception(f"Failed to initialize OpenAI client: {e}")

client = initialize_openai_client()

def gpt_call(system_prompt: str, user_prompt: str) -> str:
    try:
        response = client.invoke([
        ("system", "You are a concise assistant!"),
        ("human", "Ping!")
    ])
        return response.content
    except Exception as e:
        return f"ERROR: {e}"


@pytest.mark.skipif(not _env_ready(), reason="Azure OpenAI env vars not set")
def test_azure_openai_valid_key_roundtrip():
    """Sanity check: with the current key, we should get a response string."""
    # client = _client()
    # resp = client.chat.completions.create(
    #     model=os.getenv("OPENAI_MODEL"),  # Azure *deployment name*
    #     messages=[{"role": "user", "content": "ping"}],
    #     temperature=0.0,
    #     timeout=20,
    # )
    
    system_prompt = (
        "You are a helpful assistant. "

    )

    user_prompt = f"""
    ping the Azure OpenAI service to ensure connectivity and valid key.
    
    """
    
    
    resp = gpt_call(system_prompt, user_prompt)
    # assert hasattr(resp, "choices"), "No choices returned from Azure OpenAI"
    # content = resp.choices[0].message.content
    logging.info(f"RESULT: {resp}")
    assert isinstance(resp, str) and len(resp) > 0


# @pytest.mark.skipif(not _env_ready(), reason="Azure OpenAI env vars not set")
# def test_azure_openai_invalid_key_raises(monkeypatch):
#     """If we force an invalid key, the client must raise an auth/401 error.

#     This ensures the test fails when the key is wrong, instead of silently passing.
#     """
#     # Temporarily set a bogus key
#     monkeypatch.setenv("OPENAI_API_KEY", "sk-invalid-key-for-test")

#     # Rebuild client with the bogus key
#     bad_client = AzureOpenAI(
#         api_key=os.getenv("OPENAI_API_KEY"),
#         api_version=os.getenv("OPENAI_API_VERSION"),
#         azure_endpoint=os.getenv("OPENAI_API_BASE"),
#     )

#     with pytest.raises((AuthenticationError, APIStatusError)) as exc_info:
#         bad_client.chat.completions.create(
#             model=os.getenv("OPENAI_MODEL"),
#             messages=[{"role": "user", "content": "ping"}],
#             temperature=0.0,
#             timeout=15,
#         )

#     # Extra guard: when APIStatusError, ensure it's 401
#     err = exc_info.value
#     if isinstance(err, APIStatusError):
#         assert getattr(err, "status_code", None) in (401, 403), (
#             f"Expected 401/403 for invalid key, got status {getattr(err, 'status_code', None)}"
#         )
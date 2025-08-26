import openai
import os
from dotenv import load_dotenv

load_dotenv()

def test_openai_connection():
    openai.api_type = os.getenv("OPENAI_API_TYPE", "azure")
    openai.api_base = os.getenv("OPENAI_API_BASE")
    openai.api_key = os.getenv("OPENAI_API_KEY")
    openai.api_version = os.getenv("OPENAI_API_VERSION")

    # response = openai.ChatCompletion.create(
    #     model=os.getenv("OPENAI_MODEL"),
    #     messages=[{"role": "user", "content": "Say hello"}]
    # )


    response = openai.ChatCompletion.create(
        model=os.getenv("OPENAI_MODEL"),  # <- This is your deployment name
        messages=[
            {"role": "user", "content": "Hello from Donor Intelligence!"}
        ]
    )

    print(response.choices[0].message["content"])
    # ================= LLM API =================
    # def initialize_openai_client():
    #     try:
    #         client = openai.AzureOpenAI(
    #             api_key=os.getenv("OPENAI_API_KEY"),
    #             azure_endpoint=os.getenv("OPENAI_API_BASE"),
    #             api_version=os.getenv("OPENAI_API_VERSION"),
    #         )
    #         return client
    #     except Exception as e:
    #         raise Exception(f"Failed to initialize OpenAI client: {e}")
        
        
    # client = initialize_openai_client()
    # try:
    #     response = client.chat.completions.create(
    #         model=os.getenv("AZURE_DEPLOYMENT_NAME"),
    #         messages=[
    #             {"role": "system", "content": "You are a helpful assistant."},
    #             {"role": "user", "content": "Say Hello"}
    #         ],
    #         temperature=0.3
    #     )
    #     assert response.choices[0].message.content.strip()
    # except Exception as e:
    #     assert f"ERROR: {e}"

    

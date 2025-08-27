# 🧠 Donor Intelligence

This solution parses donor websites, strategic plans, and funding announcements in real-time to organize and summarize insights, generate donor profiles, and suggest alignment strategies for proposals.

---

## 🏛️ Architecture

This application is built on a decoupled, asynchronous architecture designed for scalability and robustness.

-   **Frontend**: A [Streamlit](https://streamlit.io/) application serves as the user interface. Users can submit requests with donor details and upload supporting PDF documents.
-   **File Storage**: Uploaded PDF documents are stored in **Azure Blob Storage**.
-   **Request Queue**: Request details, including user inputs and links to the documents in Blob Storage, are stored as items in an **Azure Cosmos DB** container. This acts as a persistent queue for the backend.
-   **Backend**: An **Azure Function**, triggered on a timer, polls the Cosmos DB for new requests. When a "pending" request is found, the function downloads the relevant documents and runs the multi-agent analysis pipeline. The backend uses a multi-agent system powered by `crewai`. The orchestration of agents is handled explicitly in `backend/main.py`, where each agent's task is executed sequentially, and typed outputs are passed manually to the next task. The final result is then used to update the request item in Cosmos DB.

This design ensures that the user interface remains responsive and is not blocked by the potentially long-running analysis process.

### Target Solution Architecture Diagram
<img width="2362" height="1141" alt="image" src="https://github.com/user-attachments/assets/aba55675-84d5-466e-a445-3728695f2f67" />

---

## 📁 Project Structure

```
.
├── backend/
│   ├── agents/
│   ├── config/
│   ├── data/
│   ├── models.py
│   ├── prompts/
│   ├── utils/
│   ├── .env.example
│   ├── function_app.py
│   ├── host.json
│   ├── local.settings.json
│   └── main.py
│
├── frontend/
│   └── streamlit_app.py
│
├── tests/
│   ├── test_main_crew.py
│   └── ...
│
├── .gitignore
├── README.md
└── requirements.txt
```

---

## ⚙️ Configuration

Before running the application, you need to configure the necessary environment variables for the Azure services. The backend service uses a `.env` file to manage these secrets.

1.  Find the `.env.example` file in the `backend/` directory.
2.  Create a copy of this file in the same `backend/` directory and name it `.env`.
3.  Fill in the values for the following variables in the `backend/.env` file:

| Variable                         | Description                                                                                             |
| -------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `OPENAI_API_BASE`                | The endpoint URL for your Azure OpenAI resource.                                                        |
| `OPENAIAPI_KEY`                  | Your API key for the Azure OpenAI service.                                                              |
| `OPENAI_API_VERSION`             | The API version for Azure OpenAI (e.g., `2024-05-01-preview`).                                          |
| `OPENAI_MODEL`                   | The **deployment name** of your model in Azure OpenAI.                                                  |
| `AZURE_STORAGE_CONNECTION_STRING`| The connection string for your Azure Storage account where PDFs will be uploaded.                       |
| `AZURE_STORAGE_CONTAINER_NAME`   | The name of the container within your storage account to use for uploads (e.g., `pdf-uploads`).         |
| `AZURE_COSMOS_CONNECTION_STRING` | The connection string for your Azure Cosmos DB account.                                                 |
| `AZURE_COSMOS_DATABASE_NAME`     | The name of the database to use within your Cosmos DB account (e.g., `DonorIntelDB`).                   |
| `AZURE_COSMOS_CONTAINER_NAME`    | The name of the container for storing requests (e.g., `Requests`).                                      |

---

## 🚀 How to Run

### Frontend (Streamlit App)

1.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **Run the Streamlit app**:
    ```bash
    streamlit run frontend/streamlit_app.py
    ```
    You can now open your browser to the local URL provided by Streamlit to use the application.

### Backend (Azure Function)

The backend is an Azure Function located in the `backend/` directory. It is designed to be deployed to Azure and run on a timer schedule.

To run it locally for development or testing, you can use the [Azure Functions Core Tools](https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local).

1.  **Configure local settings**:
    - Navigate to the `backend/` directory.
    - You will find a `local.settings.json` file. This file is used by the Azure Functions Core Tools to manage local environment variables.
    - You **must** fill in the value for `AzureWebJobsStorage` with a valid connection string to an Azure Storage account. This storage account is used by the Functions runtime for its internal operations (e.g., managing triggers and logs).
    - You also need to fill in the other placeholders for the OpenAI and Cosmos DB connection details, similar to the root `.env` file.

2.  **Navigate to the backend directory**:
    ```bash
    cd backend
    ```
3.  **Run the function app**:
    ```bash
    func start
    ```
    The function will start and begin polling for pending requests in Cosmos DB according to its schedule.

---

## 🤖 Agents

This project uses a multi-agent system powered by `crewai` to perform the donor intelligence analysis. Each agent has a specific role:

### 1. Donor Research Agent
**Goal:** Synthesize pre-scraped web data and provided documents to extract current priorities, partnerships, and funding calls.
**Inputs:** Donor name, thematic area, region, pre-scraped web data, and document content.
**Output**: Structured research bullet points with source URLs.

### 2. Profile Synthesizer Agent
**Goal:** Consolidate raw findings and existing records into a structured donor profile.
**Inputs:** Raw insights, existing profile text, context tags (region/theme).
**Output**: Drafted profile with key sections.

### 3. Report Writer Agent
**Goal:** Convert structured donor profiles into tailored documents.
**Inputs:** Synthesized profile.
**Output**: Editable Word document text.

### 4. Guidance Agent
**Goal:** Generate outreach instructions and tips.
**Inputs:** Donor name.
**Output**: Engagement instructions, application cycles, standard language.

### 5. Strategy Recommender Agent
**Goal:** Suggest strategic approach for engaging the donor.
**Inputs:** Donor profile, region, theme, recent calls or updates.
**Output**: Strategic recommendation and justification.

### 6. Governance Agent
**Goal:** Apply role-based filtering to redact sensitive content.
**Inputs:** User role, document sections.
**Output**: Redacted content.
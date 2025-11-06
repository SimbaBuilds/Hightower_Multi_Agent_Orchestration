
import os

SONNET_4_5 = "claude-sonnet-4-5-20250929" #$3,$15/M I/O
HAIKU_4_5 = "claude-haiku-4-5-20251001"
O3MINI = "o3-mini-2025-01-31" #$1,$5/M I/O
GPT_4_1 = "gpt-4.1-2025-04-14" # ROUGHLY $.1,$.5/M I/O 
GPT_4_1_MINI = "gpt-4.1-mini-2025-04-14" #ROUGHLY $.01,$.05/M I/O 
GPT_4_1_NANO = "gpt-4.1-nano-2025-04-14" # ROUGHLY $.001,$.005/M I/O
GEMINI_2_5_PRO = "gemini-2.5-pro" # $1.25,$5/M I/O
GEMINI_2_5_FLASH = "gemini-2.5-flash" # $0.075,$0.30/M I/O
CACHE_CONTENT_THRESHOLD = 1000
CACHE_MAX_CHARS = 15000  # Maximum characters to store in cache
USE_ONE_HOUR_CACHE = True  # Use 1-hour cache TTL (2x write cost) vs 5-minute (1.25x write cost)
SYSTEM_MODEL = SONNET_4_5
THIRD_PARTY_SERVICE_TIMEOUT = 240 #seconds
SERVICE_TOOL_DEFAULT_TIMEOUT = 240 #seconds - default timeout for service tools when not specified in database
SERVICE_TOOL_MAX_TIMEOUT = 3600 #seconds - maximum allowed timeout for any service tool
WEB_APP_URL = "https://juniperassistant.com"


# LLM Classifier Configuration
LLM_CLASSIFIER_MODEL = GPT_4_1_MINI
LLM_CLASSIFIER_TEMPERATURE = 0.1
MAX_PREDICTED_SERVICE_TYPES = 5



#AGENTS

# Chat Agent Configuration
CHAT_AGENT_MAX_TURNS = 10
CHAT_AGENT_SS_TRUNC_THRESHOLD = 200
CHAT_AGENT_SIMILARITY_THRESHOLD = 0.1
CHAT_AGENT_SEMANTIC_SEARCH_MAX_RESULTS = 2
# Retrieval Agent Configuration
# Integrations Agent Configuration
INTEGRATIONS_AGENT_LLM_MODEL = HAIKU_4_5
INTEGRATIONS_AGENT_MAX_TURNS = 16
INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD = 200 #for viewing and retrieval of db resources
INTEGRATIONS_AGENT_SERVICE_TOOL_TRUNC_THRESHOLD = 5000 #for viewing and retrieval of content fetched by service tools
NUM_RECENT_INTEGRATIONS_INJECTED = 20 #deprecated bc LLM classifier?
INTEGRATIONS_AGENT_RECENT_INTEGRATIONS_DEFAULT_LIMIT = 5
# Config Agent Configuration



# Search Agent Configuration
SEARCH_AGENT_DUCKDUCKGO_MAX_RESULTS = 5

# Intelligence Injection Configuration
INTELLIGENCE_MODEL_MAP = {
    1: GEMINI_2_5_PRO,      
    2: SONNET_4_5
}
INTEGRATION_COMPLETION_LLM = SONNET_4_5





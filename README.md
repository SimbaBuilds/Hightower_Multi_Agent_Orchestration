# Multi-Agent Orchestration System - Implementation Overview

## Table of Contents
- [System Architecture](#system-architecture)
- [Core Components](#core-components)
- [Agent Folder Structure](#agent-folder-structure)
- [Key Implementation Details](#key-implementation-details)
- [Agent Communication Flow](#agent-communication-flow)
- [Advanced Features](#advanced-features)

---

## System Architecture

This is a multi-agent orchestration system called **Juniper** that uses specialized AI agents to handle different types of user requests. The system is built around a base agent framework with modular, extensible agent implementations.

### High-Level Design Principles
1. **Modular Agent Architecture** - Each agent specializes in a specific domain
2. **Action-Observation Loop** - Agents use iterative reasoning with tool calls
3. **Multi-Provider Support** - Seamlessly supports OpenAI, Anthropic, Google, and xAI models
4. **Intelligent Caching** - Anthropic prompt caching for cost optimization
5. **Hierarchical Agent Communication** - Agents can call sub-agents for specialized tasks

---

## Core Components

### Base Agent (`base_agent.py`)

The foundation of the entire system. Implements the core agent loop and infrastructure.

**Key Features:**
- **Universal Action Processing** - Handles both JSON and legacy text-based action formats
- **Multi-Provider Support** - Automatic provider initialization based on model name
- **Retry & Fallback Logic** - Exponential backoff with provider fallback
- **Prompt Caching** - Anthropic-specific caching for static content
- **MAS (Multi-Agent System) Logging** - Detailed logging to database for analysis
- **Intelligence Upgrades** - Dynamic model switching based on tool requirements
- **Cancellation Support** - Request cancellation checking during execution

**Core Methods:**
```python
- query() - Main entry point for processing messages
- process_actions() - Parses and executes agent actions
- execute() - Generates model responses
- add_message() - Manages conversation history
- upgrade_intelligence() - Dynamically upgrades to more capable models
```

**Action Loop Flow:**
1. Receive user message
2. Generate model response
3. Parse action (if present)
4. Execute action handler
5. Add observation to context
6. Repeat until final response or max turns reached

---

### Models (`models.py`)

Defines core data structures:

```python
class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    type: Literal["text", "image"]
    timestamp: int

class Action(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Dict[str, Any]]
    returns: str
    example: Optional[str]
    handler: Callable  # Sync or async function
```

---

### Model Providers (`model_providers.py`)

Abstraction layer for different LLM providers with unified interface.

**Supported Providers:**
- **OpenAI** - GPT-4, o3-mini with vision support
- **Anthropic** - Claude Sonnet 4.5 with caching and vision
- **Google** - Gemini 2.5 Pro with vision
- **xAI** - Grok 3/3.5 with vision

**Key Features:**
- Retry logic with exponential backoff
- Vision support across all providers
- Provider fallback on failure
- Cache metrics logging (Anthropic)

**Retry Configuration:**
```python
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    enable_fallback: bool = True
```

---

### Prompt Templates (`prompt_templates.py`)

Builds system prompts with optional caching support.

**Key Functions:**
- `build_system_prompt()` - Creates cacheable or standard prompts
- `format_action()` - Formats actions for display in prompts

**Caching Strategy:**
Static content (context, response template, actions) is cached, while dynamic content (instructions, examples) remains uncached for flexibility.

---

## Agent Folder Structure

```
app/agents/
├── __init__.py
├── base_agent.py              # Base agent class
├── models.py                  # Data models
├── model_providers.py         # LLM provider abstraction
├── prompt_templates.py        # Prompt building utilities
│
├── chat_agent/
│   └── chat_agent.py          # Main orchestrator agent
│
├── integrations/
│   ├── integrations_agent.py  # Third-party service integration agent
│   └── service_tools/
│       ├── __init__.py
│       └── gmail_tools.py     # Example service tool
│
└── search/
    └── search.py              # Web search with XAI LiveSearch
```

---

## Key Implementation Details

### 1. Chat Agent (`chat_agent/chat_agent.py`)

The primary orchestrator and user-facing agent.

**Role:**
- Direct interface with users
- Routes requests to specialized sub-agents
- Manages conversation context and semantic search
- Coordinates settings updates and integration workflows

**Available Actions:**
- `call_integrations_agent` - Delegate to integrations agent
- `web_search` - Search the web (Brave/DuckDuckGo/XAI LiveSearch)
- `call_retrieval_agent` - Access user memories and documents
- `call_config_agent` - Update user settings
- `fetch_from_cache` - Retrieve cached content
- `call_wellness_agent` - Health and wellness support

**Key Features:**
- Semantic search on user memories for context injection
- Content caching for large context items
- Settings update detection via markers
- Integration state tracking

**Configuration:**
```python
model=SYSTEM_MODEL  # User's preferred model
temperature=0.1
max_turns=CHAT_AGENT_MAX_TURNS
enable_caching=True  # Anthropic prompt caching
```

---

### 2. Integrations Agent (`integrations/integrations_agent.py`)

Handles third-party service integrations and tool execution.

**Role:**
- Discovers available service tools
- Fetches tool definitions and resources
- Executes service tools with proper context
- Manages integration setup workflows

**Available Actions:**
```python
1. initial_md_fetch - Get tool names/descriptions for a service
2. fetch_tool_data - Get full tool definition + resources
3. fetch_recently_used_integrations - Get user's recent services
4. use_service_tool - Execute a service tool
5. fetch_service_integration_scripts - Get integration scripts
6. call_retrieval_agent - Access memories and documents
7. fetch_from_cache - Retrieve cached content
```

**Workflow:**
```
User Request → LLM Classifier (predict service types)
           ↓
Filter Recent Integrations by predicted types
           ↓
IntegrationsAgent with filtered context
           ↓
1. initial_md_fetch (get tools + resources truncated)
2. fetch_tool_data (get full tool + specific resources)
3. use_service_tool (execute with params)
           ↓
Return result to Chat Agent
```

**Key Features:**
- **LLM Classification** - Predicts relevant service types to reduce context
- **Intelligent Intelligence** - Auto-upgrades model based on tool requirements
- **Resource Management** - Fetches truncated content first, full content on demand
- **Auto-Append Logic** - Automatically includes user email/phone for SMS/email tools
- **Content Caching** - Large service tool responses are auto-cached
- **Send Validation** - Requires "send" keyword in user message for send operations

**Service Tool Execution:**
- Timeout enforcement (configurable per tool)
- User-specific tool registry
- Async handler support
- Comprehensive error logging

---

### 3. Search Module (`search/search.py`)

Provides web search capabilities with multiple fallback options.

**Search Hierarchy:**
1. **XAI LiveSearch** (if enabled) - Real-time search via Grok with citations
2. **Brave Search API** - Primary search provider
3. **DuckDuckGo** - Fallback search engine
4. **Alternative engines** - Additional fallbacks

**XAI LiveSearch Features:**
- Real-time data with source citations
- Configurable sources: web, news, X platform
- User preference-based enablement
- Usage tracking and billing integration

**Configuration:**
```python
user_search_config = {
    'enabled_system_integrations': {
        'xai_live_search': True/False
    }
}
```

---

## Agent Communication Flow

### Request Processing Lifecycle

```
1. User Request → Chat Agent
   ├─ Semantic Search (inject relevant memories)
   ├─ Check for cached content
   └─ Generate initial response

2. Chat Agent → Sub-Agent (if needed)
   ├─ Create action with request context
   ├─ Sub-agent processes with own tools
   └─ Return result to Chat Agent

3. Sub-Agent Execution
   ├─ Think (generate thought)
   ├─ Act (call tools/actions)
   ├─ Observe (receive tool results)
   └─ Repeat or Respond

4. Response Assembly
   ├─ Process observation embedding ($$$observation$$$)
   ├─ Check for markers ([SETTINGS_UPDATED], [INTEGRATION_IN_PROGRESS])
   └─ Return to user
```

### Action Format

Agents respond with structured JSON:

```json
{
  "thought": "Reasoning about what to do",
  "type": "action|response",
  "action": {
    "name": "action_name",
    "parameters": {...}
  }
}
```

Or for final responses:

```json
{
  "thought": "Reasoning about the response",
  "type": "response",
  "response": "Final answer to user"
}
```

---

## Advanced Features

### 1. Prompt Caching (Anthropic)

Reduces costs by caching static prompt content.

**Cache Structure:**
```python
[
  {
    "type": "text",
    "text": "Static content (context, actions, template)",
    "cache_control": {"type": "ephemeral", "ttl": "1h"}
  },
  {
    "type": "text",
    "text": "Dynamic content (instructions, examples)"
    # No cache control - always fresh
  }
]
```

**Benefits:**
- Up to 90% cost reduction on cached tokens
- Faster response times on cache hits
- Automatic cache metrics logging

### 2. Content Caching System

Manages large content (resources, service tool responses) in per-request cache.

**Auto-Caching Triggers:**
- Chat Agent: Content > 2000 chars (configurable)
- Integrations Agent: Service tool response > 3000 chars (configurable)

**Cache Key Format:**
```python
f"chat_context_{resource_id}_{uuid4()}"
f"service_tool_{tool_name}_{uuid4()}"
```

**Usage:**
```python
# Store
RequestCacheService.store(request_id, cache_key, content)

# Retrieve via action
fetch_from_cache: {"cache_key": "chat_context_123_abc"}
```

### 3. Intelligence Upgrades

Agents can dynamically upgrade to more capable models.

```python
def upgrade_intelligence(self, required_level: int):
    """Map: 1→base, 2→mid, 3→high, 4→highest"""
    new_model = INTELLIGENCE_MODEL_MAP[required_level]
    self.model = new_model
    self._reinitialize_provider()
```

**Use Cases:**
- Service tools requiring advanced reasoning
- Complex integration workflows
- Vision/multimodal tasks

### 4. MAS (Multi-Agent System) Logging

Comprehensive logging to database for analysis and debugging.

**Logged Events:**
- User requests
- Agent thoughts
- Actions taken
- Observations received
- Final responses
- Errors and timeouts

**Schema:**
```python
{
  'request_id': str,
  'user_id': str,
  'type': 'user_request|thought|action|observation|response',
  'turn': int,
  'agent_name': str,
  'content': str,
  'model': str,
  'action_name': str (optional),
  'action_params': dict (optional),
  'metadata': dict,
  'created_at': timestamp
}
```

### 5. Request Cancellation

Users can cancel long-running requests.

**Implementation:**
```python
def check_cancellation():
    if request_id and supabase:
        # Check cancellation_requests table
        # Check requests.status
        if cancelled:
            raise HTTPException(status_code=499)

# Called at:
- Before each agent iteration
- After action execution
- Before sub-agent calls
```

### 6. Provider Fallback

Automatic failover to alternative LLM providers.

**Fallback Order:**
```python
['anthropic', 'google', 'openai', 'xai']
```

**Process:**
1. Primary provider fails
2. Check if fallback enabled
3. Try each provider with API key
4. Log fallback success/failure
5. Raise original error if all fail

---

## Configuration

Key configuration options (from `app/config/config.py`):

```python
# Models
SYSTEM_MODEL = "claude-sonnet-4-5-20250929"
INTEGRATIONS_AGENT_LLM_MODEL = "claude-sonnet-4-5-20250929"
CONFIG_AGENT_LLM_MODEL = "claude-sonnet-4-5-20250929"

# Caching
USE_ONE_HOUR_CACHE = True  # vs 5min

# Max Turns
CHAT_AGENT_MAX_TURNS = 5
INTEGRATIONS_AGENT_MAX_TURNS = 10

# Content Thresholds
CHAT_AGENT_SS_TRUNC_THRESHOLD = 2000
INTEGRATIONS_AGENT_RESOURCE_TRUNC_THRESHOLD = 2000
INTEGRATIONS_AGENT_SERVICE_TOOL_TRUNC_THRESHOLD = 3000

# Search
SEARCH_AGENT_DUCKDUCKGO_MAX_RESULTS = 5

# Service Tools
SERVICE_TOOL_DEFAULT_TIMEOUT = 30  # seconds
SERVICE_TOOL_MAX_TIMEOUT = 120

# Semantic Search
CHAT_AGENT_SEMANTIC_SEARCH_MAX_RESULTS = 5
CHAT_AGENT_SIMILARITY_THRESHOLD = 0.3
```

---

## Extension Points

### Adding a New Agent

1. Create agent file in appropriate directory
2. Extend `BaseAgent` class
3. Define specialized actions
4. Implement handler functions
5. Create action factory function
6. Register with calling agent

```python
class MyNewAgent(BaseAgent):
    def __init__(self, user_id, supabase, model=MODEL, **kwargs):
        tools = create_my_agent_tools(user_id, supabase)
        context = "My agent's role and capabilities"
        instructions = "Specific instructions for this agent"

        super().__init__(
            actions=tools,
            additional_context=context,
            general_instructions=instructions,
            model=model,
            max_turns=10,
            agent_name="My Agent",
            enable_caching=True
        )

def create_my_agent_action(user_id, supabase, model, request_id):
    async def handle_request(request: str) -> str:
        agent = MyNewAgent(user_id, supabase, model)
        message = Message(role="user", content=request, ...)
        return await agent.query([message], user_id, request_id, supabase)

    return Action(
        name="call_my_agent",
        description="What this agent does",
        parameters={"request": {"type": "string", ...}},
        returns="What it returns",
        handler=handle_request
    )
```

### Adding a Service Tool

1. Define tool in `service_tools` table (database)
2. Implement handler function
3. Register in service registry
4. Associate with service and tags

Service tools are dynamically loaded from the database and executed via the integrations agent.

---

## Testing & Debugging

### Logging

Structured logging at multiple levels:
- Agent initialization
- Action execution
- Tool calls
- Cache operations
- Provider fallbacks
- Errors and warnings

### MAS Logs Analysis

Query the `mas_logs` table to analyze agent behavior:

```sql
SELECT * FROM mas_logs
WHERE request_id = 'xxx'
ORDER BY created_at;
```

### Cache Metrics

Monitor cache efficiency in Anthropic provider logs:

```
🔄 CACHE METRICS - Model: claude-sonnet-4-5-20250929
  📝 Cache Write: 1234 tokens (15% of input)
  📖 Cache Read: 5678 tokens (70% of input)
  💾 Total Input: 8100 tokens, Output: 450 tokens
  ✅ Cache Status: HIT
```

---

## Dependencies

Core dependencies:
- `openai` - OpenAI API client
- `anthropic` - Via HTTP requests (no SDK)
- `google-genai` - Google Gemini client
- `supabase` - Database and auth
- `fastapi` - API framework
- `pydantic` - Data validation
- `requests` - HTTP client
- `duckduckgo_search` - Search fallback

---

## Summary

This multi-agent system provides a robust, extensible framework for building AI applications with:

- **Modularity** - Clean separation of concerns with specialized agents
- **Reliability** - Retry logic, fallbacks, and error handling
- **Efficiency** - Prompt caching and content management
- **Observability** - Comprehensive logging and metrics
- **Flexibility** - Multi-provider support and dynamic model switching
- **Scalability** - Async operations and timeout management

The `/agents` folder contains all the core agent logic, with the `base_agent.py` serving as the foundation that all specialized agents build upon. The system uses a hierarchical agent architecture where the Chat Agent orchestrates calls to specialized sub-agents (Integrations, Retrieval, Config, etc.) to fulfill user requests efficiently.

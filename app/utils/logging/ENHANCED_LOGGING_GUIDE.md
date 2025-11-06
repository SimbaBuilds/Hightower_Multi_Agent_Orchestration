# Enhanced Logging Infrastructure Guide

## Overview

The logging infrastructure has been enhanced to support comprehensive querying of agent activities, service tool calls, resource retrievals, and agent outputs. This guide covers the new logging capabilities and how to use them.

## Key Features

### 1. System Prompt Logging
- Every agent initialization now logs the full system prompt
- Query system prompts by agent name
- Useful for debugging agent behavior and understanding context

### 2. Service Tool Call Logging
- Detailed logging before, during, and after service tool execution
- Captures tool name, parameters, results, and execution time
- Tracks success/failure states with error details

### 3. Resource Retrieval Logging
- Logs when resources are retrieved by tag for tools
- Tracks resource count and IDs
- Links resources to specific tools

### 4. Agent Output Logging
- Logs intermediate observations during agent processing
- Captures final responses with preview and length
- Tracks the full agent execution flow

## Enhanced CLI Commands

### Query System Prompts
```bash
# View all system prompts
python app/utils/logging/query_logs.py --system-prompts

# View system prompts for specific agent
python app/utils/logging/query_logs.py --system-prompts --agent 'Integrations Agent'
```

### Query Service Tool Calls
```bash
# View all service tool calls
python app/utils/logging/query_logs.py --service-tools

# View calls for specific tool
python app/utils/logging/query_logs.py --service-tools --tool-name send_email

# Verbose mode shows parameters and results
python app/utils/logging/query_logs.py --service-tools --verbose
```

### Query Resource Retrievals
```bash
# View all resource retrievals
python app/utils/logging/query_logs.py --resources

# Verbose mode shows resource IDs
python app/utils/logging/query_logs.py --resources --verbose
```

### Query by Agent
```bash
# View all logs for specific agent
python app/utils/logging/query_logs.py --agent 'Integrations Agent'

# Filter by action type
python app/utils/logging/query_logs.py --agent 'Integrations Agent' --action observation
```

### Query by User
```bash
# View all logs for specific user
python app/utils/logging/query_logs.py --user-id <user-uuid>

# Combine with other filters
python app/utils/logging/query_logs.py --user-id <user-uuid> --service-tools
```

### Advanced Queries
```bash
# View agent observations
python app/utils/logging/query_logs.py --action observation

# View final responses
python app/utils/logging/query_logs.py --action final_response

# Get summary of agent activity
python app/utils/logging/query_logs.py --summary --component agent

# JSON output for programmatic processing
python app/utils/logging/query_logs.py --agent 'Test Agent' --json
```

## Programmatic Access

### Using LogAnalyzer
```python
from app.utils.logging.log_analyzer import LogAnalyzer

analyzer = LogAnalyzer('logs/app.log')

# Get agent logs
agent_logs = analyzer.get_agent_logs(agent_name="Integrations Agent")

# Get service tool calls
tool_calls = analyzer.filter_logs(action='service_tool_call_start')

# Get resources retrieved
resources = analyzer.filter_logs(action='resources_retrieved')

# Get system prompts
prompts = analyzer.filter_logs(action='agent_initialization')
```

### Custom Logging in Agents
```python
from app.utils.logging.component_loggers import get_agent_logger, log_agent_event

logger = get_agent_logger("My Agent", __name__)

# Log service tool call
log_agent_event(
    logger,
    "Executing service tool",
    agent_name="My Agent",
    user_id=str(user_id),
    action="service_tool_call_start",
    tool_name=tool_name,
    tool_parameters=params,
    service_name=service_name
)

# Log resource retrieval
log_agent_event(
    logger,
    f"Retrieved {len(resources)} resources",
    agent_name="My Agent",
    user_id=str(user_id),
    action="resources_retrieved",
    tool_name=tool_name,
    resource_count=len(resources),
    resource_ids=[r['id'] for r in resources]
)
```

## Log Actions Reference

| Action | Description | Key Fields |
|--------|-------------|------------|
| `agent_initialization` | Agent created with system prompt | agent_name, model, system_prompt |
| `query_start` | Query processing started | user_id, request_id, message_count |
| `service_tool_call_start` | Service tool execution started | tool_name, tool_parameters, service_name |
| `service_tool_call_success` | Service tool completed successfully | tool_name, result_preview, duration_ms |
| `service_tool_call_error` | Service tool failed | tool_name, error_type, error_message |
| `resources_retrieved` | Resources fetched by tag | tool_name, resource_count, resource_ids |
| `observation` | Agent produced intermediate result | observation, action_count |
| `final_response` | Agent produced final response | response_preview, response_length |
| `query_complete` | Query processing completed | actions_executed, settings_updated |

## Best Practices

1. **Use Structured Logging**: Always use the provided logging functions instead of raw logger calls
2. **Include Context**: Always include user_id, agent_name, and relevant context
3. **Log Tool Parameters**: When logging service tools, include sanitized parameters
4. **Track Durations**: Use duration_ms for performance monitoring
5. **Preview Long Content**: Use response_preview and observation fields with truncation

## Troubleshooting

### No Logs Found
- Check that the log file exists at `logs/app.log`
- Ensure the logging configuration is properly initialized
- Verify that the component is using the enhanced logging functions

### Missing Context
- Make sure to pass all relevant context fields when logging
- Use the specialized logging functions (log_agent_event, log_integration_event)
- Check that the agent_name is set correctly in the logger

### Query Performance
- Use specific filters to reduce the number of logs processed
- Consider time-based filtering for large log files
- Use the --limit flag to restrict results
# Structured Logging Guide

## Overview

This application now uses a robust structured logging system that provides:
- **Centralized collection** with differentiated functionality tracking
- **Structured JSON logs** for easy querying and analysis
- **Component-based organization** for different app areas
- **Rich context** with user IDs, integration IDs, service names, etc.
- **Performance tracking** with automatic timing
- **Human-readable console output** with structured file logs

## Quick Start

### Basic Usage

```python
# Replace old logging
import logging
logger = logging.getLogger(__name__)

# With new structured logging
from app.utils.logging.component_loggers import get_integration_logger, log_integration_event
logger = get_integration_logger(__name__)
```

### Component-Specific Loggers

```python
# Integration Builder
from app.utils.logging.component_loggers import get_integration_logger
logger = get_integration_logger(__name__)

# Agents
from app.utils.logging.component_loggers import get_agent_logger
logger = get_agent_logger('config_agent', __name__)

# API Endpoints
from app.utils.logging.component_loggers import get_api_logger
logger = get_api_logger(__name__)

# Search Functionality
from app.utils.logging.component_loggers import get_search_logger
logger = get_search_logger(__name__)
```

### Structured Event Logging

```python
from app.utils.logging.component_loggers import log_integration_event, log_agent_event, log_api_event

# Integration events
log_integration_event(
    logger,
    "Starting service integration",
    service_name="slack",
    user_id="user123",
    action="integration_start"
)

# Agent events
log_agent_event(
    logger,
    "Processing user request",
    agent_name="config_agent",
    user_id="user123",
    action="request_processing"
)

# API events
log_api_event(
    logger,
    "Request completed",
    endpoint="/chat",
    method="POST",
    status_code=200,
    duration_ms=245.5
)
```

## Querying Logs

### Command Line Tool

```bash
# Show recent integration builder logs
python scripts/query_logs.py --component integration_builder

# Show only error logs
python scripts/query_logs.py --errors

# Show summary for a component
python scripts/query_logs.py --summary --component agent

# Show logs in JSON format
python scripts/query_logs.py --component api --json

# Limit results
python scripts/query_logs.py --component integration_builder --limit 20
```

### Programmatic Analysis

```python
from app.utils.logging.log_analyzer import LogAnalyzer

analyzer = LogAnalyzer()

# Get integration logs for a specific service
integration_logs = analyzer.get_integration_logs(service_name="slack")

# Get error logs for last 24 hours
error_logs = analyzer.get_error_logs()

# Generate component summary
summary = analyzer.generate_component_summary("integration_builder")

# Search for specific text
search_results = analyzer.search_logs("oauth")
```

## Migration Guide

### Step 1: Update Imports

Replace:
```python
import logging
logger = logging.getLogger(__name__)
```

With:
```python
from app.utils.logging.component_loggers import get_integration_logger  # or appropriate logger
logger = get_integration_logger(__name__)
```

### Step 2: Add Context to Important Logs

Replace:
```python
logger.info(f"Processing request for {service_name}")
```

With:
```python
log_integration_event(
    logger,
    f"Processing request for {service_name}",
    service_name=service_name,
    user_id=user_id,
    action="request_processing"
)
```

### Step 3: Use Performance Decorators

```python
from app.utils.logging.component_loggers import log_function_calls

@log_function_calls(logger)
def expensive_operation():
    # This will automatically log entry, exit, and timing
    pass
```

## Log Structure

Each log entry contains:

```json
{
  "timestamp": "2024-01-15T10:30:45.123456Z",
  "level": "INFO",
  "logger": "app.agents.integration_builder",
  "message": "Starting service integration",
  "component": "integration_builder",
  "user_id": "user123",
  "service_name": "slack",
  "action": "integration_start",
  "module": "integration_builder",
  "pathname": "/path/to/file.py",
  "lineno": 42,
  "funcName": "start_integration"
}
```

## Configuration

Set environment variables to customize logging:

```bash
export LOG_LEVEL=DEBUG          # DEBUG, INFO, WARNING, ERROR, CRITICAL
export LOG_CONSOLE=true         # Enable/disable console output
export LOG_FILE=true            # Enable/disable file output
export LOG_DIR=logs             # Directory for log files
```

## Best Practices

### 1. Use Appropriate Log Levels
- **DEBUG**: Detailed diagnostic information
- **INFO**: General application flow
- **WARNING**: Something unexpected but not an error
- **ERROR**: Error conditions
- **CRITICAL**: Serious errors that may cause the application to abort

### 2. Include Relevant Context
Always include relevant IDs and context:
```python
log_integration_event(
    logger,
    "OAuth callback received",
    service_name=service_name,
    user_id=user_id,
    integration_id=integration_id,
    action="oauth_callback"
)
```

### 3. Use Consistent Action Names
Use descriptive action names that can be filtered:
- `integration_start`, `integration_complete`, `integration_error`
- `request_start`, `request_complete`, `request_error`
- `research_start`, `research_complete`, `config_generated`

### 4. Log Performance-Critical Operations
```python
import time
start_time = time.time()
# ... do work ...
duration = time.time() - start_time

log_integration_event(
    logger,
    "Service research completed",
    service_name=service_name,
    user_id=user_id,
    action="research_complete",
    duration_seconds=duration
)
```

## Examples

### Integration Builder Example

```python
from app.utils.logging.component_loggers import get_integration_logger, log_integration_event

logger = get_integration_logger(__name__)

class ServiceIntegrator:
    def __init__(self, user_id: str):
        self.user_id = user_id
    
    def integrate_service(self, service_name: str):
        log_integration_event(
            logger,
            f"Starting integration for {service_name}",
            service_name=service_name,
            user_id=self.user_id,
            action="integration_start"
        )
        
        try:
            # Integration logic here
            result = self._perform_integration(service_name)
            
            log_integration_event(
                logger,
                f"Integration completed successfully for {service_name}",
                service_name=service_name,
                user_id=self.user_id,
                action="integration_complete",
                integration_id=result.integration_id
            )
            
            return result
            
        except Exception as e:
            log_integration_event(
                logger,
                f"Integration failed for {service_name}: {str(e)}",
                level="ERROR",
                service_name=service_name,
                user_id=self.user_id,
                action="integration_error",
                error_type=type(e).__name__,
                error_message=str(e)
            )
            raise
```

### Agent Example

```python
from app.utils.logging.component_loggers import get_agent_logger, log_agent_event

logger = get_agent_logger('chat_agent', __name__)

class ChatAgent:
    def process_message(self, message: str, user_id: str):
        log_agent_event(
            logger,
            f"Processing message from user {user_id}",
            agent_name="chat_agent",
            user_id=user_id,
            action="message_processing"
        )
        
        # Process message...
        response = self._generate_response(message)
        
        log_agent_event(
            logger,
            "Message processing completed",
            agent_name="chat_agent",
            user_id=user_id,
            action="message_complete"
        )
        
        return response
```

## Troubleshooting

### Common Issues

1. **Missing logs**: Ensure `setup_logging_from_env()` is called in `main.py`
2. **No component field**: Use component loggers instead of basic `logging.getLogger()`
3. **Logs not structured**: Check that file output is enabled and using `StructuredFormatter`

### Debugging

```python
# Quick way to check if logging is working
from app.utils.logging.component_loggers import get_api_logger
logger = get_api_logger('test')
logger.info("Test message", extra={'test_field': 'test_value'})
```

### Log Analysis

```python
# Check recent errors
from app.utils.logging.log_analyzer import quick_error_summary
print(quick_error_summary())

# Check component activity
from app.utils.logging.log_analyzer import quick_component_logs
recent_logs = quick_component_logs('integration_builder')
```

This structured logging system provides powerful insights into your application behavior while maintaining excellent performance and developer experience. 
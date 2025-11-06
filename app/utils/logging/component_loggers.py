import logging
from typing import Optional, Dict, Any
from functools import wraps
import time

class ComponentLoggerAdapter(logging.LoggerAdapter):
    """Adapter that automatically adds component context to log records."""
    
    def __init__(self, logger, component: str, extra_context: Dict[str, Any] = None):
        self.component = component
        self.extra_context = extra_context or {}
        super().__init__(logger, {})
    
    def process(self, msg, kwargs):
        # Add component and any extra context to the log record
        # Start with logger's default context, then overlay with explicit parameters
        extra = self.extra_context.copy() if self.extra_context else {}
        extra['component'] = self.component
        extra.update(kwargs.get('extra', {}))  # Explicit parameters take precedence
        kwargs['extra'] = extra
        return msg, kwargs

def get_component_logger(component_name: str, module_name: str = None, **extra_context) -> ComponentLoggerAdapter:
    """
    Get a logger configured for a specific component.
    
    Args:
        component_name: Name of the component (e.g., 'integration_builder', 'chat_agent')
        module_name: Python module name (defaults to __name__ in calling context)
        **extra_context: Additional context to include in all log records
    
    Returns:
        ComponentLoggerAdapter configured for the component
    """
    logger_name = module_name or f"app.{component_name}"
    base_logger = logging.getLogger(logger_name)
    return ComponentLoggerAdapter(base_logger, component_name, extra_context)

# Specialized logging functions for common scenarios
def log_integration_event(logger, message: str, level: str = "INFO", **context):
    """
    Log integration-related events with structured context.
    
    Args:
        logger: Logger instance
        message: Log message
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        **context: Additional context (service_name, integration_id, user_id, action, etc.)
    """
    extra = {}
    
    # Standard integration context fields
    for field in ['service_name', 'integration_id', 'user_id', 'action', 'session_id']:
        if field in context:
            extra[field] = context[field]
    
    # Add any other context
    for key, value in context.items():
        if key not in extra:
            extra[key] = value
    
    getattr(logger, level.lower())(message, extra=extra)

def log_agent_event(logger, message: str, level: str = "INFO", **context):
    """
    Log agent-related events with structured context.
    
    Args:
        logger: Logger instance
        message: Log message
        level: Log level
        **context: Additional context (agent_name, user_id, action, etc.)
    """
    extra = {}
    
    # Standard agent context fields
    for field in ['agent_name', 'user_id', 'action', 'session_id', 'request_id']:
        if field in context:
            extra[field] = context[field]
    
    # Add any other context
    for key, value in context.items():
        if key not in extra:
            extra[key] = value
    
    getattr(logger, level.lower())(message, extra=extra)

def log_api_event(logger, message: str, level: str = "INFO", **context):
    """
    Log API-related events with structured context.
    
    Args:
        logger: Logger instance
        message: Log message
        level: Log level
        **context: Additional context (endpoint, method, status_code, user_id, etc.)
    """
    extra = {}
    
    # Standard API context fields
    for field in ['endpoint', 'method', 'status_code', 'user_id', 'request_id', 'duration_ms']:
        if field in context:
            extra[field] = context[field]
    
    # Add any other context
    for key, value in context.items():
        if key not in extra:
            extra[key] = value
    
    getattr(logger, level.lower())(message, extra=extra)

def log_performance_event(logger, message: str, duration: float, level: str = "INFO", **context):
    """
    Log performance-related events with timing data.
    
    Args:
        logger: Logger instance
        message: Log message
        duration: Duration in seconds
        level: Log level
        **context: Additional context
    """
    extra = {'duration_seconds': duration, 'duration_ms': round(duration * 1000, 2)}
    extra.update(context)
    
    getattr(logger, level.lower())(message, extra=extra)

# Decorators for automatic logging
def log_function_calls(logger, component: str = None):
    """
    Decorator to automatically log function entry/exit with timing.
    
    Args:
        logger: Logger instance or component logger
        component: Component name (if not using component logger)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            start_time = time.time()
            
            # Log function entry
            extra = {'action': f"{func_name}_start"}
            if component:
                extra['component'] = component
            
            logger.info(f"Starting {func_name}", extra=extra)
            
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                
                # Log successful completion
                extra = {
                    'action': f"{func_name}_complete",
                    'duration_seconds': duration,
                    'duration_ms': round(duration * 1000, 2)
                }
                if component:
                    extra['component'] = component
                
                logger.info(f"Completed {func_name} in {duration:.3f}s", extra=extra)
                return result
                
            except Exception as e:
                duration = time.time() - start_time
                
                # Log error
                extra = {
                    'action': f"{func_name}_error",
                    'duration_seconds': duration,
                    'duration_ms': round(duration * 1000, 2),
                    'error_type': type(e).__name__,
                    'error_message': str(e)
                }
                if component:
                    extra['component'] = component
                
                logger.error(f"Error in {func_name} after {duration:.3f}s: {e}", extra=extra)
                raise
                
        return wrapper
    return decorator

# Pre-configured component loggers for your main application areas
def get_integration_logger(module_name: str = None, **extra_context):
    """Get logger for integration builder components."""
    return get_component_logger('integration_builder', module_name, **extra_context)

def get_agent_logger(agent_name: str, module_name: str = None, **extra_context):
    """Get logger for agent components."""
    context = {'agent_name': agent_name}
    context.update(extra_context)
    return get_component_logger('agent', module_name, **context)

def get_api_logger(module_name: str = None, **extra_context):
    """Get logger for API endpoints."""
    return get_component_logger('api', module_name, **extra_context)

def get_search_logger(module_name: str = None, **extra_context):
    """Get logger for search functionality."""
    return get_component_logger('search', module_name, **extra_context)

def get_config_logger(module_name: str = None, **extra_context):
    """Get logger for configuration management."""
    return get_component_logger('config', module_name, **extra_context) 
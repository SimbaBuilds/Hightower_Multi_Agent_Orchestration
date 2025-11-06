import logging
import logging.handlers
import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

class StructuredFormatter(logging.Formatter):
    """Custom formatter that creates structured JSON log entries."""
    
    def format(self, record):
        # Base structured log entry
        log_entry = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': getattr(record, 'module', record.name.split('.')[-1]),
            'pathname': record.pathname,
            'lineno': record.lineno,
            'funcName': record.funcName
        }
        
        # Add custom fields if they exist
        if hasattr(record, 'component'):
            log_entry['component'] = record.component
        if hasattr(record, 'user_id'):
            log_entry['user_id'] = record.user_id
        if hasattr(record, 'integration_id'):
            log_entry['integration_id'] = record.integration_id
        if hasattr(record, 'service_name'):
            log_entry['service_name'] = record.service_name
        if hasattr(record, 'action'):
            log_entry['action'] = record.action
        if hasattr(record, 'session_id'):
            log_entry['session_id'] = record.session_id
        if hasattr(record, 'agent_name'):
            log_entry['agent_name'] = record.agent_name
        if hasattr(record, 'request_id'):
            log_entry['request_id'] = record.request_id
        if hasattr(record, 'system_prompt'):
            log_entry['system_prompt'] = record.system_prompt
        if hasattr(record, 'agent_name'):
            log_entry['agent_name'] = record.agent_name
            
        # Add exception info if present
        if record.exc_info:
            log_entry['exception'] = self.formatException(record.exc_info)
            
        return json.dumps(log_entry, default=str)

class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter for console output."""
    
    def format(self, record):
        # Color codes for different levels
        colors = {
            'DEBUG': '\033[36m',    # Cyan
            'INFO': '\033[32m',     # Green
            'WARNING': '\033[33m',  # Yellow
            'ERROR': '\033[31m',    # Red
            'CRITICAL': '\033[35m', # Magenta
        }
        reset = '\033[0m'
        
        # Get component info if available
        component = getattr(record, 'component', 'app')
        action = getattr(record, 'action', '')
        
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        
        # Build the log line
        color = colors.get(record.levelname, '')
        level = f"{color}{record.levelname:8}{reset}"
        
        # Component and action info
        context = f"[{component}"
        if action:
            context += f":{action}"
        context += "]"
        
        return f"{timestamp} {level} {context:20} {record.getMessage()}"


def setup_logging(
    log_level: str = "INFO",
    console_output: bool = True,
    file_output: bool = True,
    log_dir: str = "logs",
    log_filename: str = "app.log",
    max_file_size_mb: int = 10,
    backup_count: int = 5
):
    """Configure application-wide structured logging."""
    
    # Create logs directory if it doesn't exist
    Path(log_dir).mkdir(exist_ok=True)
    
    # Get root logger and clear existing handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console handler with human-readable format
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ConsoleFormatter())
        console_handler.setLevel(logging.INFO)  # Less verbose for console
        root_logger.addHandler(console_handler)
    
    # File handler with structured JSON format
    if file_output:
        log_file = os.path.join(log_dir, log_filename)
        
        # Ensure log file exists
        Path(log_file).touch(exist_ok=True)
        
        # Use rotating file handler to prevent huge log files
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_file_size_mb * 1024 * 1024,  # Convert MB to bytes
            backupCount=backup_count
        )
        file_handler.setFormatter(StructuredFormatter())
        file_handler.setLevel(max(logging.INFO, getattr(logging, log_level.upper())))
        root_logger.addHandler(file_handler)
    
    # Set up specific logger levels for noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    return root_logger

def get_app_logger(name: str = None):
    """Get a logger for general app usage."""
    logger_name = name or 'app'
    return logging.getLogger(logger_name)

def setup_logging_with_custom_file(
    log_filename: str,
    log_level: str = "INFO",
    console_output: bool = True,
    file_output: bool = True,
    log_dir: str = "logs",
    max_file_size_mb: int = 10,
    backup_count: int = 5
):
    """Configure production logging with a custom log file name."""
    return setup_logging(
        log_level=log_level,
        console_output=console_output,
        file_output=file_output,
        log_dir=log_dir,
        log_filename=log_filename,
        max_file_size_mb=max_file_size_mb,
        backup_count=backup_count
    )

def setup_logging_with_test_files(
    test_name: str = None,
    log_level: str = "INFO",
    console_output: bool = True,
    file_output: bool = True,
    log_dir: str = "logs",
    max_file_size_mb: int = 10,
    backup_count: int = 5
):
    """Configure application-wide structured logging with optional test-specific log files."""
    
    # First setup normal logging
    setup_logging(
        log_level=log_level,
        console_output=console_output,
        file_output=file_output,
        log_dir=log_dir,
        max_file_size_mb=max_file_size_mb,
        backup_count=backup_count
    )
    
    # Add test-specific file handler if test_name is provided
    if test_name:
        # Create logs directory if it doesn't exist
        Path(log_dir).mkdir(exist_ok=True)
        
        test_log_file = os.path.join(log_dir, f"{test_name}.log")
        
        # Ensure test log file exists
        Path(test_log_file).touch(exist_ok=True)
        
        # Use rotating file handler for test logs
        test_handler = logging.handlers.RotatingFileHandler(
            test_log_file,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count
        )
        test_handler.setFormatter(StructuredFormatter())
        test_handler.setLevel(max(logging.INFO, getattr(logging, log_level.upper())))
        
        # Create or get test-specific logger
        test_logger = logging.getLogger(test_name)
        test_logger.addHandler(test_handler)
        test_logger.setLevel(logging.DEBUG)  # More verbose for test logs
        
        # Prevent propagation to avoid duplicate entries in main app.log
        test_logger.propagate = False
        
        # Also add console handler to test logger so we can see test output
        if console_output:
            test_console_handler = logging.StreamHandler()
            test_console_handler.setFormatter(ConsoleFormatter())
            test_console_handler.setLevel(logging.INFO)
            test_logger.addHandler(test_console_handler)
    
    return logging.getLogger(test_name) if test_name else logging.getLogger()

# Environment-based configuration
def setup_logging_from_env():
    """Setup logging based on environment variables."""
    log_level = os.getenv('LOG_LEVEL', 'INFO')
    console_output = os.getenv('LOG_CONSOLE', 'true').lower() == 'true'
    file_output = os.getenv('LOG_FILE', 'true').lower() == 'true'
    log_dir = os.getenv('LOG_DIR', 'logs')
    
    return setup_logging(
        log_level=log_level,
        console_output=console_output,
        file_output=file_output,
        log_dir=log_dir
    ) 
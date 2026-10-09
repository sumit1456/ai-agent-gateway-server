"""
Logging configuration with Windows Unicode support.
"""
import logging
import sys
import io


def setup_logging():
    """
    Configure logging with UTF-8 encoding for Windows compatibility.
    This handles emoji and Unicode characters in log messages.
    """
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # File handler with UTF-8 encoding
    file_handler = logging.FileHandler('gateway.log', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    
    # Stream handler with UTF-8 encoding
    if sys.platform == 'win32':
        # Windows: Handle console encoding issues
        try:
            # Try to reconfigure stdout/stderr to UTF-8
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
            stream_handler = logging.StreamHandler(sys.stdout)
        except (AttributeError, OSError):
            # Fallback: Use TextIOWrapper with error handling
            # This replaces unencodable characters instead of crashing
            stream_handler = logging.StreamHandler(
                io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            )
    else:
        # Unix-like systems: Standard stream handler
        stream_handler = logging.StreamHandler(sys.stdout)
    
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove any existing handlers
    root_logger.handlers.clear()
    
    # Add our handlers
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.
    This ensures all loggers use the same configuration.
    """
    return logging.getLogger(name)

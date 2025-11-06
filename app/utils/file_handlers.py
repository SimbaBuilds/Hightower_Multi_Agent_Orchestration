"""
File Handling Utilities

Utility functions for file processing and validation.
"""

import mimetypes
from typing import Optional
from pathlib import Path


def get_file_type_from_filename(filename: str) -> Optional[str]:
    """
    Get file type from filename extension.

    Args:
        filename: The filename to analyze

    Returns:
        File type (extension without dot) or None if not determinable
    """
    if not filename:
        return None

    # Get file extension
    extension = Path(filename).suffix.lower()

    if extension:
        # Remove the dot and return
        return extension[1:]

    return None


def get_mime_type(filename: str) -> Optional[str]:
    """
    Get MIME type from filename.

    Args:
        filename: The filename to analyze

    Returns:
        MIME type or None if not determinable
    """
    if not filename:
        return None

    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type


def is_supported_medical_file(filename: str) -> bool:
    """
    Check if filename represents a supported medical record file type.

    Args:
        filename: The filename to check

    Returns:
        True if supported, False otherwise
    """
    supported_extensions = {'pdf', 'jpeg', 'jpg', 'png', 'csv'}
    file_type = get_file_type_from_filename(filename)

    return file_type is not None and file_type.lower() in supported_extensions


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in bytes to human readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string
    """
    if size_bytes == 0:
        return "0 B"

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def validate_file_metadata(file_metadata: dict) -> tuple[bool, str]:
    """
    Validate file metadata for medical records processing.

    Args:
        file_metadata: Dictionary containing file metadata

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ['url', 'file_type', 'filename', 'size_bytes']

    # Check required fields
    for field in required_fields:
        if field not in file_metadata:
            return False, f"Missing required field: {field}"

        if not file_metadata[field] and field != 'size_bytes':  # size_bytes can be 0
            return False, f"Empty value for required field: {field}"

    # Validate file type
    if not is_supported_medical_file(file_metadata['filename']):
        return False, f"Unsupported file type: {file_metadata['file_type']}"

    # Validate size
    try:
        size = int(file_metadata['size_bytes'])
        if size < 0:
            return False, "File size cannot be negative"
        if size > 100 * 1024 * 1024:  # 100MB limit
            return False, "File size exceeds 100MB limit"
    except (ValueError, TypeError):
        return False, "Invalid file size format"

    # Validate URL
    url = file_metadata['url']
    if not url.startswith(('http://', 'https://')):
        return False, "Invalid URL format"

    return True, ""
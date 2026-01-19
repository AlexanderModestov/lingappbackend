import logging
from pathlib import Path
from typing import Union

from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)


def parse_document(file_path: Union[str, Path]) -> str:
    """
    Parse a document (PDF, DOCX, etc.) and extract text content.

    Uses IBM's docling library for robust document parsing that handles
    complex layouts across various formats.

    Args:
        file_path: Path to the document file

    Returns:
        Extracted text content from the document

    Raises:
        ValueError: If file doesn't exist or parsing fails
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise ValueError(f"File not found: {file_path}")

    logger.info(f"Parsing document: {file_path}")

    try:
        converter = DocumentConverter()
        result = converter.convert(str(file_path))

        # Extract text from the conversion result
        text = result.document.export_to_markdown()

        if not text or not text.strip():
            raise ValueError(f"No text content extracted from {file_path}")

        logger.info(
            f"Successfully parsed document: {file_path} "
            f"({len(text)} characters extracted)"
        )
        return text.strip()

    except Exception as e:
        logger.error(f"Error parsing document {file_path}: {e}")
        raise ValueError(f"Failed to parse document: {str(e)}")


def get_supported_extensions() -> list[str]:
    """Get list of supported file extensions."""
    return [".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".html", ".md", ".txt"]


def is_supported_file(file_path: Union[str, Path]) -> bool:
    """Check if the file type is supported for parsing."""
    file_path = Path(file_path)
    return file_path.suffix.lower() in get_supported_extensions()

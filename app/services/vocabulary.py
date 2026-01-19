import json
import logging
from typing import List

from openai import OpenAI

from app.core.config import get_settings
from app.models.schemas import ExtractedFlashcard, FlashcardCreate

logger = logging.getLogger(__name__)

# Token limits for chunking
MAX_TOKENS_PER_CHUNK = 5000
OVERLAP_TOKENS = 500
CHARS_PER_TOKEN_ESTIMATE = 4  # Rough estimate for English text


def split_text_into_chunks(text: str) -> List[str]:
    """
    Split text into overlapping chunks for processing.

    Uses a simple character-based approach with estimated token counts.
    """
    max_chars = MAX_TOKENS_PER_CHUNK * CHARS_PER_TOKEN_ESTIMATE
    overlap_chars = OVERLAP_TOKENS * CHARS_PER_TOKEN_ESTIMATE

    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + max_chars

        # Try to break at sentence boundary
        if end < len(text):
            # Look for sentence ending punctuation
            break_point = text.rfind(". ", start + max_chars // 2, end)
            if break_point == -1:
                break_point = text.rfind("? ", start + max_chars // 2, end)
            if break_point == -1:
                break_point = text.rfind("! ", start + max_chars // 2, end)
            if break_point != -1:
                end = break_point + 1

        chunks.append(text[start:end].strip())
        start = end - overlap_chars

    return chunks


def extract_vocabulary_from_chunk(
    client: OpenAI, text: str, chunk_index: int, total_chunks: int
) -> List[ExtractedFlashcard]:
    """Extract vocabulary from a single text chunk using OpenAI."""

    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_vocabulary",
                "description": "Save extracted vocabulary flashcards",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "flashcards": {
                            "type": "array",
                            "description": "List of vocabulary flashcards",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "term": {
                                        "type": "string",
                                        "description": "The vocabulary word or phrase in English",
                                    },
                                    "translation": {
                                        "type": "string",
                                        "description": "Translation to Russian (or explanation if no direct translation)",
                                    },
                                    "definition": {
                                        "type": "string",
                                        "description": "Clear definition of the term in English",
                                    },
                                    "context_original": {
                                        "type": "string",
                                        "description": "The original sentence from the text where this term appears",
                                    },
                                    "grammar_note": {
                                        "type": "string",
                                        "description": "Optional grammar information (e.g., 'noun', 'phrasal verb', 'adjective')",
                                    },
                                },
                                "required": [
                                    "term",
                                    "translation",
                                    "definition",
                                    "context_original",
                                ],
                            },
                        }
                    },
                    "required": ["flashcards"],
                },
            },
        }
    ]

    system_prompt = """You are an expert English linguist and language teacher.
Your task is to analyze the provided text and extract 10-15 key vocabulary terms (words or phrases)
that would be valuable for a B2/C1 English learner.

Selection criteria:
- Focus on useful, practical vocabulary (not overly common words like "the", "is", "have")
- Include idiomatic expressions, phrasal verbs, and collocations when present
- Prioritize terms that have nuanced meanings or are challenging for non-native speakers
- Include academic or domain-specific vocabulary if relevant to the text

For each term, provide:
1. The term itself (exact form from the text)
2. A Russian translation (or brief explanation if no direct translation exists)
3. A clear English definition
4. The original sentence where it appears (for context)
5. A grammar note when helpful (part of speech, usage pattern, etc.)

Use the save_vocabulary function to submit your extracted vocabulary."""

    user_prompt = f"""Analyze this text (part {chunk_index + 1} of {total_chunks}) and extract key vocabulary:

---
{text}
---

Extract 10-15 vocabulary terms suitable for B2/C1 English learners."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "save_vocabulary"}},
            temperature=0.3,
        )

        # Extract tool call arguments
        tool_call = response.choices[0].message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)

        flashcards = [ExtractedFlashcard(**card) for card in args.get("flashcards", [])]
        return flashcards

    except Exception as e:
        logger.error(f"Error extracting vocabulary from chunk {chunk_index}: {e}")
        return []


def deduplicate_flashcards(
    flashcards: List[ExtractedFlashcard],
) -> List[FlashcardCreate]:
    """Remove duplicate terms (case-insensitive) and convert to FlashcardCreate."""
    seen_terms = set()
    unique_cards = []

    for card in flashcards:
        term_lower = card.term.lower().strip()
        if term_lower not in seen_terms:
            seen_terms.add(term_lower)
            unique_cards.append(
                FlashcardCreate(
                    term=card.term,
                    translation=card.translation,
                    definition=card.definition,
                    context_original=card.context_original,
                    grammar_note=card.grammar_note,
                )
            )

    return unique_cards


def extract_keywords_from_text(text: str) -> List[FlashcardCreate]:
    """
    Extract key vocabulary from text using Map-Reduce strategy.

    Strategy:
    1. Map: Split text into chunks, extract vocabulary from each chunk
    2. Reduce: Aggregate results, deduplicate terms

    Args:
        text: Source text to analyze

    Returns:
        List of unique FlashcardCreate objects
    """
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    # Split text into manageable chunks
    chunks = split_text_into_chunks(text)
    logger.info(f"Processing text in {len(chunks)} chunk(s)")

    # Map: Extract vocabulary from each chunk
    all_flashcards: List[ExtractedFlashcard] = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing chunk {i + 1}/{len(chunks)}")
        chunk_flashcards = extract_vocabulary_from_chunk(
            client, chunk, i, len(chunks)
        )
        all_flashcards.extend(chunk_flashcards)
        logger.info(f"Extracted {len(chunk_flashcards)} terms from chunk {i + 1}")

    # Reduce: Deduplicate and consolidate
    unique_flashcards = deduplicate_flashcards(all_flashcards)
    logger.info(
        f"Total unique vocabulary terms extracted: {len(unique_flashcards)} "
        f"(from {len(all_flashcards)} raw extractions)"
    )

    return unique_flashcards

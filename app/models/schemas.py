from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


# Enums
class SourceType(str, Enum):
    YOUTUBE = "youtube"
    FILE = "file"
    URL = "url"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewQuality(str, Enum):
    FORGOT = "forgot"
    KNOW = "know"


# User Schemas
class UserProfile(BaseModel):
    id: UUID
    email: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime


# Material Schemas
class MaterialBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


class MaterialCreateYouTube(MaterialBase):
    url: HttpUrl


class MaterialCreateFile(MaterialBase):
    pass  # File is handled separately via multipart form


class MaterialResponse(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    source_type: SourceType
    source_url: Optional[str] = None
    file_path: Optional[str] = None
    processed_text: Optional[str] = None
    processing_status: ProcessingStatus
    created_at: datetime

    class Config:
        from_attributes = True


class MaterialStatus(BaseModel):
    id: UUID
    processing_status: ProcessingStatus


class MaterialWithFlashcards(MaterialResponse):
    flashcards: List["FlashcardResponse"] = []


# Flashcard Schemas
class FlashcardBase(BaseModel):
    term: str = Field(..., min_length=1, max_length=200)
    translation: str = Field(..., min_length=1, max_length=500)
    definition: Optional[str] = None
    context_original: Optional[str] = None
    grammar_note: Optional[str] = None


class FlashcardCreate(FlashcardBase):
    """Schema for creating flashcards (used internally by vocabulary service)."""

    pass


class FlashcardResponse(FlashcardBase):
    id: UUID
    material_id: UUID
    user_id: UUID
    learning_stage: int
    next_review_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class FlashcardReview(BaseModel):
    quality: ReviewQuality


class FlashcardReviewResponse(BaseModel):
    id: UUID
    learning_stage: int
    next_review_at: datetime


# OpenAI Tool Calling Schema
class ExtractedFlashcard(BaseModel):
    """Schema for OpenAI tool calling - vocabulary extraction."""

    term: str = Field(..., description="The vocabulary word or phrase in English")
    translation: str = Field(
        ..., description="Translation to Russian (or explanation if no direct translation)"
    )
    definition: str = Field(
        ..., description="Clear definition of the term in English"
    )
    context_original: str = Field(
        ..., description="The original sentence from the text where this term appears"
    )
    grammar_note: Optional[str] = Field(
        None, description="Optional grammar information (e.g., 'noun', 'phrasal verb')"
    )


class ExtractedVocabulary(BaseModel):
    """Container for extracted vocabulary from OpenAI."""

    flashcards: List[ExtractedFlashcard] = Field(
        ..., description="List of extracted vocabulary flashcards"
    )


# Update forward references
MaterialWithFlashcards.model_rebuild()

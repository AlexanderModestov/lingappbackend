import logging
import tempfile
import uuid
from pathlib import Path
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from supabase import Client

from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.models.schemas import (
    MaterialCreateYouTube,
    MaterialResponse,
    MaterialStatus,
    MaterialWithFlashcards,
    ProcessingStatus,
    SourceType,
)
from app.services.doc_parser import is_supported_file, parse_document
from app.services.vocabulary import extract_keywords_from_text
from app.services.yt_parser import extract_transcript

router = APIRouter(prefix="/materials", tags=["Materials"])
logger = logging.getLogger(__name__)


def process_material_background(
    material_id: str,
    user_id: str,
    source_type: str,
    source_url: str | None,
    file_path: str | None,
    supabase: Client,
):
    """Background task to process material and extract vocabulary.

    Note: This is a synchronous function so FastAPI runs it in a thread pool,
    preventing it from blocking the main event loop.
    """
    try:
        logger.info(f"Starting processing for material {material_id}")

        # Extract text based on source type
        if source_type == SourceType.YOUTUBE and source_url:
            text = extract_transcript(source_url)
        elif source_type == SourceType.FILE and file_path:
            # Download file from Supabase Storage
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(file_path).suffix
            ) as tmp_file:
                file_data = supabase.storage.from_("uploads").download(file_path)
                tmp_file.write(file_data)
                tmp_file.flush()
                text = parse_document(tmp_file.name)
        else:
            raise ValueError(f"Invalid source type or missing source: {source_type}")

        # Extract vocabulary
        flashcards = extract_keywords_from_text(text)
        logger.info(f"Extracted {len(flashcards)} flashcards for material {material_id}")

        # Save flashcards to database
        for card in flashcards:
            supabase.table("flashcards").insert(
                {
                    "material_id": material_id,
                    "user_id": user_id,
                    "term": card.term,
                    "translation": card.translation,
                    "definition": card.definition,
                    "context_original": card.context_original,
                    "grammar_note": card.grammar_note,
                }
            ).execute()

        # Update material status and save processed text
        supabase.table("materials").update(
            {
                "processing_status": ProcessingStatus.COMPLETED,
                "processed_text": text[:50000],  # Limit stored text size
            }
        ).eq("id", material_id).execute()

        logger.info(f"Successfully completed processing for material {material_id}")

    except Exception as e:
        logger.error(f"Error processing material {material_id}: {e}")
        supabase.table("materials").update(
            {"processing_status": ProcessingStatus.FAILED}
        ).eq("id", material_id).execute()


@router.post("/upload/youtube", response_model=MaterialResponse)
async def upload_youtube_material(
    data: MaterialCreateYouTube,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> MaterialResponse:
    """Create a new material from a YouTube URL."""
    result = (
        supabase.table("materials")
        .insert(
            {
                "user_id": str(current_user.id),
                "title": data.title,
                "source_type": SourceType.YOUTUBE,
                "source_url": str(data.url),
                "processing_status": ProcessingStatus.PENDING,
            }
        )
        .execute()
    )

    return MaterialResponse(**result.data[0])


@router.post("/upload/file", response_model=MaterialResponse)
async def upload_file_material(
    title: str = Form(...),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> MaterialResponse:
    """Upload a file (PDF, DOCX) and create a new material."""
    # Validate file type
    file_ext = Path(file.filename or "").suffix.lower()
    if not is_supported_file(file.filename or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file_ext}",
        )

    # Generate unique file path
    file_id = str(uuid.uuid4())
    storage_path = f"{current_user.id}/{file_id}{file_ext}"

    # Upload to Supabase Storage
    content = await file.read()
    supabase.storage.from_("uploads").upload(
        storage_path,
        content,
        file_options={"content-type": file.content_type or "application/octet-stream"},
    )

    # Create material record
    result = (
        supabase.table("materials")
        .insert(
            {
                "user_id": str(current_user.id),
                "title": title,
                "source_type": SourceType.FILE,
                "file_path": storage_path,
                "processing_status": ProcessingStatus.PENDING,
            }
        )
        .execute()
    )

    return MaterialResponse(**result.data[0])


@router.post("/{material_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_material(
    material_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
):
    """Trigger asynchronous processing of a material."""
    # Get material and verify ownership
    result = (
        supabase.table("materials")
        .select("*")
        .eq("id", material_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    material = result.data

    # Check if already processing or completed
    if material["processing_status"] in [
        ProcessingStatus.PROCESSING,
        ProcessingStatus.COMPLETED,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material is already {material['processing_status']}",
        )

    # Update status to processing
    supabase.table("materials").update(
        {"processing_status": ProcessingStatus.PROCESSING}
    ).eq("id", material_id).execute()

    # Start background processing
    background_tasks.add_task(
        process_material_background,
        material_id=material_id,
        user_id=str(current_user.id),
        source_type=material["source_type"],
        source_url=material.get("source_url"),
        file_path=material.get("file_path"),
        supabase=supabase,
    )

    return {"message": "Processing started", "material_id": material_id}


@router.get("/{material_id}/status", response_model=MaterialStatus)
async def get_material_status(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> MaterialStatus:
    """Get the processing status of a material."""
    result = (
        supabase.table("materials")
        .select("id, processing_status")
        .eq("id", material_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    return MaterialStatus(**result.data)


@router.get("", response_model=List[MaterialResponse])
async def list_materials(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> List[MaterialResponse]:
    """List all materials for the current user."""
    result = (
        supabase.table("materials")
        .select("*")
        .eq("user_id", str(current_user.id))
        .order("created_at", desc=True)
        .execute()
    )

    return [MaterialResponse(**m) for m in result.data]


@router.get("/{material_id}", response_model=MaterialWithFlashcards)
async def get_material(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> MaterialWithFlashcards:
    """Get a material with its flashcards."""
    # Get material
    material_result = (
        supabase.table("materials")
        .select("*")
        .eq("id", material_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not material_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    # Get flashcards
    flashcards_result = (
        supabase.table("flashcards")
        .select("*")
        .eq("material_id", material_id)
        .order("created_at", desc=False)
        .execute()
    )

    return MaterialWithFlashcards(
        **material_result.data, flashcards=flashcards_result.data
    )


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
):
    """Delete a material and its flashcards."""
    # Verify ownership
    result = (
        supabase.table("materials")
        .select("id, file_path")
        .eq("id", material_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    # Delete file from storage if exists
    if result.data.get("file_path"):
        try:
            supabase.storage.from_("uploads").remove([result.data["file_path"]])
        except Exception as e:
            logger.warning(f"Failed to delete file from storage: {e}")

    # Delete material (cascades to flashcards)
    supabase.table("materials").delete().eq("id", material_id).execute()

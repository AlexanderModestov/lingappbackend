import logging
from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.services.chat import get_chat_response
from app.services.subscription import check_chat_access

router = APIRouter(prefix="/chat", tags=["Chat"])
logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    id: UUID
    material_id: UUID
    role: str
    content: str
    created_at: datetime


class ChatSend(BaseModel):
    message: str


class ChatResponse(BaseModel):
    user_message: ChatMessage
    assistant_message: ChatMessage


@router.get("/{material_id}", response_model=List[ChatMessage])
async def get_chat_history(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> List[ChatMessage]:
    """Get chat history for a material."""
    # Verify material ownership
    material_result = (
        supabase.table("materials")
        .select("id")
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

    # Get chat messages
    result = (
        supabase.table("chat_messages")
        .select("*")
        .eq("material_id", material_id)
        .eq("user_id", str(current_user.id))
        .order("created_at", desc=False)
        .execute()
    )

    return [ChatMessage(**msg) for msg in result.data]


@router.post("/{material_id}", response_model=ChatResponse)
async def send_message(
    material_id: str,
    data: ChatSend,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> ChatResponse:
    """Send a message and get AI response."""
    # Check chat access (Pro only)
    if not check_chat_access(current_user.id, supabase):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Chat is only available for Pro users",
                "code": "CHAT_ACCESS_DENIED",
                "upgrade_url": "/api/v1/payments/create-checkout-session",
            },
        )

    # Get material and verify ownership
    material_result = (
        supabase.table("materials")
        .select("id, title, processed_text, processing_status")
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

    material = material_result.data

    if material["processing_status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material must be processed before chatting",
        )

    if not material.get("processed_text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material has no processed text",
        )

    # Get existing chat history
    history_result = (
        supabase.table("chat_messages")
        .select("role, content")
        .eq("material_id", material_id)
        .eq("user_id", str(current_user.id))
        .order("created_at", desc=False)
        .execute()
    )

    chat_history = history_result.data

    # Save user message
    user_msg_result = (
        supabase.table("chat_messages")
        .insert({
            "material_id": material_id,
            "user_id": str(current_user.id),
            "role": "user",
            "content": data.message,
        })
        .execute()
    )

    user_message = ChatMessage(**user_msg_result.data[0])

    # Generate AI response
    try:
        assistant_content = get_chat_response(
            material_text=material["processed_text"],
            material_title=material["title"],
            chat_history=chat_history,
            user_message=data.message,
        )
    except Exception as e:
        logger.error(f"Failed to generate chat response: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate response",
        )

    # Save assistant message
    assistant_msg_result = (
        supabase.table("chat_messages")
        .insert({
            "material_id": material_id,
            "user_id": str(current_user.id),
            "role": "assistant",
            "content": assistant_content,
        })
        .execute()
    )

    assistant_message = ChatMessage(**assistant_msg_result.data[0])

    return ChatResponse(
        user_message=user_message,
        assistant_message=assistant_message,
    )


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_chat_history(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
):
    """Clear chat history for a material."""
    # Verify material ownership
    material_result = (
        supabase.table("materials")
        .select("id")
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

    # Delete all chat messages for this material
    supabase.table("chat_messages").delete().eq(
        "material_id", material_id
    ).eq("user_id", str(current_user.id)).execute()

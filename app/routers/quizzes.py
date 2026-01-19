import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.core.security import CurrentUser, get_current_user, get_supabase_client
from app.services.quiz import generate_quiz

router = APIRouter(prefix="/quizzes", tags=["Quizzes"])
logger = logging.getLogger(__name__)


class QuizOption(BaseModel):
    text: str
    is_correct: bool


class QuizQuestion(BaseModel):
    question: str
    question_type: str
    options: List[QuizOption]
    correct_answer: str
    explanation: str


class QuizResponse(BaseModel):
    id: UUID
    material_id: UUID
    questions: List[QuizQuestion]
    score: Optional[int] = None
    total_questions: int
    completed_at: Optional[datetime] = None
    created_at: datetime


class QuizCreate(BaseModel):
    material_id: UUID
    num_questions: int = 5


class QuizSubmit(BaseModel):
    answers: List[str]  # User's answers in order


class QuizResult(BaseModel):
    quiz_id: UUID
    score: int
    total_questions: int
    results: List[dict]  # Per-question results


@router.post("", response_model=QuizResponse)
async def create_quiz(
    data: QuizCreate,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> QuizResponse:
    """Generate a new quiz for a material."""
    # Get material and verify ownership
    material_result = (
        supabase.table("materials")
        .select("id, processed_text, processing_status")
        .eq("id", str(data.material_id))
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
            detail="Material must be processed before creating a quiz",
        )

    if not material.get("processed_text"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Material has no processed text",
        )

    # Generate quiz questions
    try:
        questions = generate_quiz(material["processed_text"], data.num_questions)
    except Exception as e:
        logger.error(f"Failed to generate quiz: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate quiz questions",
        )

    # Save quiz to database
    result = (
        supabase.table("quizzes")
        .insert({
            "material_id": str(data.material_id),
            "user_id": str(current_user.id),
            "questions": questions,
            "total_questions": len(questions),
        })
        .execute()
    )

    quiz_data = result.data[0]
    return QuizResponse(
        id=quiz_data["id"],
        material_id=quiz_data["material_id"],
        questions=quiz_data["questions"],
        score=quiz_data.get("score"),
        total_questions=quiz_data["total_questions"],
        completed_at=quiz_data.get("completed_at"),
        created_at=quiz_data["created_at"],
    )


@router.get("/material/{material_id}", response_model=List[QuizResponse])
async def list_quizzes(
    material_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> List[QuizResponse]:
    """List all quizzes for a material."""
    result = (
        supabase.table("quizzes")
        .select("*")
        .eq("material_id", material_id)
        .eq("user_id", str(current_user.id))
        .order("created_at", desc=True)
        .execute()
    )

    return [QuizResponse(**q) for q in result.data]


@router.get("/{quiz_id}", response_model=QuizResponse)
async def get_quiz(
    quiz_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> QuizResponse:
    """Get a specific quiz."""
    result = (
        supabase.table("quizzes")
        .select("*")
        .eq("id", quiz_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found",
        )

    return QuizResponse(**result.data)


@router.post("/{quiz_id}/submit", response_model=QuizResult)
async def submit_quiz(
    quiz_id: str,
    data: QuizSubmit,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
) -> QuizResult:
    """Submit quiz answers and get results."""
    # Get quiz
    result = (
        supabase.table("quizzes")
        .select("*")
        .eq("id", quiz_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found",
        )

    quiz = result.data
    questions = quiz["questions"]

    if len(data.answers) != len(questions):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected {len(questions)} answers, got {len(data.answers)}",
        )

    # Grade the quiz
    score = 0
    results = []
    for i, (question, user_answer) in enumerate(zip(questions, data.answers)):
        correct_answer = question["correct_answer"]
        is_correct = user_answer.lower().strip() == correct_answer.lower().strip()

        if is_correct:
            score += 1

        results.append({
            "question_index": i,
            "question": question["question"],
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "is_correct": is_correct,
            "explanation": question.get("explanation", ""),
        })

    # Update quiz with score
    supabase.table("quizzes").update({
        "score": score,
        "completed_at": datetime.utcnow().isoformat(),
    }).eq("id", quiz_id).execute()

    return QuizResult(
        quiz_id=quiz["id"],
        score=score,
        total_questions=len(questions),
        results=results,
    )


@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quiz(
    quiz_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: Client = Depends(get_supabase_client),
):
    """Delete a quiz."""
    result = (
        supabase.table("quizzes")
        .select("id")
        .eq("id", quiz_id)
        .eq("user_id", str(current_user.id))
        .single()
        .execute()
    )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz not found",
        )

    supabase.table("quizzes").delete().eq("id", quiz_id).execute()

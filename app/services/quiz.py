import json
import logging
from typing import List

from openai import OpenAI
from pydantic import BaseModel, Field

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class QuizOption(BaseModel):
    """A single option for a multiple choice question."""
    text: str
    is_correct: bool


class QuizQuestion(BaseModel):
    """A single quiz question."""
    question: str
    question_type: str = Field(description="Type: multiple_choice, true_false, or fill_blank")
    options: List[QuizOption] = Field(default_factory=list)
    correct_answer: str = Field(description="The correct answer text")
    explanation: str = Field(description="Why this answer is correct")


class QuizQuestions(BaseModel):
    """Container for generated quiz questions."""
    questions: List[QuizQuestion]


def generate_quiz(text: str, num_questions: int = 5) -> List[dict]:
    """
    Generate quiz questions from material text using OpenAI.

    Args:
        text: The material text to generate questions from
        num_questions: Number of questions to generate

    Returns:
        List of quiz question dictionaries
    """
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    # Truncate text if too long
    max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "..."

    system_prompt = """You are an expert educator creating quiz questions to test comprehension.
Generate varied question types:
- Multiple choice (4 options, 1 correct)
- True/False
- Fill in the blank

Questions should test understanding, not just memorization.
Include explanations for why each answer is correct.
Make questions progressively harder."""

    user_prompt = f"""Based on this content, generate {num_questions} quiz questions:

{text}

Generate a mix of question types. For multiple choice, provide 4 options.
Return as JSON with this structure:
{{
  "questions": [
    {{
      "question": "The question text",
      "question_type": "multiple_choice" | "true_false" | "fill_blank",
      "options": [
        {{"text": "Option A", "is_correct": false}},
        {{"text": "Option B", "is_correct": true}},
        ...
      ],
      "correct_answer": "The correct answer text",
      "explanation": "Why this is the correct answer"
    }}
  ]
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        result = json.loads(response.choices[0].message.content)
        questions = result.get("questions", [])

        logger.info(f"Generated {len(questions)} quiz questions")
        return questions

    except Exception as e:
        logger.error(f"Error generating quiz: {e}")
        raise ValueError(f"Failed to generate quiz: {str(e)}")

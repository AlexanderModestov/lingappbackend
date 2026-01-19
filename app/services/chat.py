import logging
from typing import List

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def get_chat_response(
    material_text: str,
    material_title: str,
    chat_history: List[dict],
    user_message: str,
) -> str:
    """
    Generate a chat response about the material using OpenAI.

    Args:
        material_text: The processed text from the material
        material_title: Title of the material
        chat_history: Previous messages in the conversation
        user_message: The user's new message

    Returns:
        AI assistant's response
    """
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    # Truncate material text if too long
    max_chars = 10000
    if len(material_text) > max_chars:
        material_text = material_text[:max_chars] + "..."

    system_prompt = f"""You are a helpful tutor discussing the following learning material.
Help the user understand the content, answer questions, explain concepts, and provide insights.

Material Title: {material_title}

Material Content:
{material_text}

Guidelines:
- Answer questions based on the material content
- Explain concepts clearly and simply
- Provide examples when helpful
- If asked about something not in the material, say so but offer related insights
- Be encouraging and supportive
- Use markdown formatting for better readability"""

    # Build messages list
    messages = [{"role": "system", "content": system_prompt}]

    # Add chat history (limit to last 10 messages to save tokens)
    for msg in chat_history[-10:]:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Add the new user message
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
        )

        assistant_message = response.choices[0].message.content
        logger.info(f"Generated chat response for material: {material_title}")
        return assistant_message

    except Exception as e:
        logger.error(f"Error generating chat response: {e}")
        raise ValueError(f"Failed to generate response: {str(e)}")

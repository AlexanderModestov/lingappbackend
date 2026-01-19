import logging
import os
import re
import tempfile
from typing import Optional

from openai import OpenAI
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"(?:embed\/)([0-9A-Za-z_-]{11})",
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript_from_api(video_id: str) -> Optional[str]:
    """Try to get transcript using YouTube Transcript API."""
    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB"]
        )
        # Consolidate transcript segments into a single string
        text = " ".join(segment["text"] for segment in transcript_list)
        return text.strip()
    except (NoTranscriptFound, TranscriptsDisabled) as e:
        logger.warning(f"No transcript available via API for {video_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching transcript for {video_id}: {e}")
        return None


def compress_audio(input_path: str, output_path: str, target_size_mb: int = 24) -> bool:
    """Compress audio file to fit within size limit using FFmpeg."""
    import subprocess
    import shutil

    if not shutil.which("ffmpeg"):
        logger.error("FFmpeg not available for compression")
        return False

    try:
        # Get duration
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())

        # Calculate bitrate to achieve target size (in kbps)
        target_bitrate = int((target_size_mb * 8 * 1024) / duration)
        # Minimum bitrate of 32kbps for intelligibility
        target_bitrate = max(32, min(target_bitrate, 128))

        logger.info(f"Compressing audio: duration={duration:.1f}s, target_bitrate={target_bitrate}kbps")

        # Compress with FFmpeg
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vn",  # No video
            "-ac", "1",  # Mono
            "-ar", "16000",  # 16kHz sample rate (good for speech)
            "-b:a", f"{target_bitrate}k",
            output_path
        ], capture_output=True, check=True)

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"Error compressing audio: {e}")
        return False


def split_audio(input_path: str, temp_dir: str, chunk_duration: int = 600) -> list:
    """Split audio into chunks of specified duration (default 10 minutes)."""
    import subprocess
    import shutil

    if not shutil.which("ffmpeg"):
        logger.error("FFmpeg not available for splitting")
        return []

    try:
        # Get total duration
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip())

        chunks = []
        start = 0
        chunk_num = 0

        while start < duration:
            chunk_path = os.path.join(temp_dir, f"chunk_{chunk_num}.mp3")
            subprocess.run([
                "ffmpeg", "-y", "-i", input_path,
                "-ss", str(start),
                "-t", str(chunk_duration),
                "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
                chunk_path
            ], capture_output=True, check=True)

            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                chunks.append(chunk_path)

            start += chunk_duration
            chunk_num += 1

        return chunks
    except Exception as e:
        logger.error(f"Error splitting audio: {e}")
        return []


def transcribe_with_whisper(video_id: str) -> Optional[str]:
    """Download audio and transcribe with OpenAI Whisper API."""
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)

    # Whisper API limit is 25MB
    MAX_FILE_SIZE = 24 * 1024 * 1024  # 24MB to be safe

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            import yt_dlp
            import shutil

            # Check if FFmpeg is available
            ffmpeg_available = shutil.which("ffmpeg") is not None

            if ffmpeg_available:
                # Use FFmpeg to extract MP3
                ydl_opts = {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "64",  # Lower quality for smaller files
                        }
                    ],
                    "outtmpl": os.path.join(temp_dir, f"{video_id}.%(ext)s"),
                    "quiet": False,
                    "no_warnings": False,
                }
            else:
                # Download audio directly without conversion
                logger.warning("FFmpeg not found, downloading audio without conversion")
                ydl_opts = {
                    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
                    "outtmpl": os.path.join(temp_dir, f"{video_id}.%(ext)s"),
                    "quiet": False,
                    "no_warnings": False,
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}",
                    download=True
                )
                if not info:
                    logger.error(f"Failed to get video info for {video_id}")
                    return None

            # Find the downloaded audio file
            audio_path = None
            for ext in ["mp3", "m4a", "webm", "opus", "ogg", "wav"]:
                potential_path = os.path.join(temp_dir, f"{video_id}.{ext}")
                if os.path.exists(potential_path) and os.path.getsize(potential_path) > 0:
                    audio_path = potential_path
                    break

            if not audio_path:
                files = os.listdir(temp_dir)
                logger.error(f"No audio file found for {video_id}. Files in temp dir: {files}")
                return None

            file_size = os.path.getsize(audio_path)
            logger.info(f"Audio file found: {audio_path} ({file_size / 1024 / 1024:.1f} MB)")

            # Handle large files
            if file_size > MAX_FILE_SIZE:
                logger.info("File too large, attempting to compress/split...")

                if ffmpeg_available:
                    # Try compression first
                    compressed_path = os.path.join(temp_dir, "compressed.mp3")
                    if compress_audio(audio_path, compressed_path):
                        compressed_size = os.path.getsize(compressed_path)
                        logger.info(f"Compressed to {compressed_size / 1024 / 1024:.1f} MB")

                        if compressed_size <= MAX_FILE_SIZE:
                            audio_path = compressed_path
                        else:
                            # Still too large, split into chunks
                            logger.info("Still too large, splitting into chunks...")
                            chunks = split_audio(audio_path, temp_dir)
                            if chunks:
                                transcripts = []
                                for i, chunk_path in enumerate(chunks):
                                    logger.info(f"Transcribing chunk {i+1}/{len(chunks)}")
                                    with open(chunk_path, "rb") as audio_file:
                                        transcript = client.audio.transcriptions.create(
                                            model="whisper-1", file=audio_file, response_format="text"
                                        )
                                        transcripts.append(transcript.strip())
                                return " ".join(transcripts)
                else:
                    logger.error("File too large and FFmpeg not available for compression")
                    return None

            # Transcribe with Whisper
            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1", file=audio_file, response_format="text"
                )
                return transcript.strip()

        except Exception as e:
            logger.error(f"Error transcribing video {video_id}: {e}", exc_info=True)
            return None


def extract_transcript(url: str) -> str:
    """
    Extract transcript from YouTube video.

    Primary method: YouTube Transcript API
    Fallback: yt-dlp + OpenAI Whisper

    Args:
        url: YouTube video URL

    Returns:
        Extracted transcript text

    Raises:
        ValueError: If URL is invalid or transcript extraction fails
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    logger.info(f"Extracting transcript for video: {video_id}")

    # Try YouTube Transcript API first
    transcript = get_transcript_from_api(video_id)
    if transcript:
        logger.info(f"Successfully extracted transcript via API for {video_id}")
        return transcript

    # Fallback to Whisper transcription
    logger.info(f"Falling back to Whisper transcription for {video_id}")
    transcript = transcribe_with_whisper(video_id)
    if transcript:
        logger.info(f"Successfully transcribed video {video_id} with Whisper")
        return transcript

    raise ValueError(
        f"Failed to extract transcript for video {video_id}. "
        "No transcript available and Whisper transcription failed."
    )

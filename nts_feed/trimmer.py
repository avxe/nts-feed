"""Audio trimming utilities for NTS downloads."""

import subprocess
import os
import logging

logger = logging.getLogger(__name__)


def trim_audio_file(file_path, trim_start=12, trim_end=12, trim_duration=None):
    """
    Trim `trim_start` seconds from the start and `trim_end` seconds from the end while
    preserving metadata and artwork. `trim_duration` is deprecated: when provided, it
    sets both start and end to that value.
    Returns the path to the trimmed file on success, None on failure.
    """
    if trim_duration is not None:
        trim_start = trim_end = trim_duration
    trim_start = max(0, int(trim_start))
    trim_end = max(0, int(trim_end))

    temp_file = None
    try:
        # Get total duration of the file using ffprobe
        result = subprocess.run(
            [
                'ffprobe',
                '-i', file_path,
                '-show_entries', 'format=duration',
                '-v', 'quiet',
                '-of', 'csv=p=0'
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        total_duration = float(result.stdout.strip())

        # Calculate new duration after trimming
        new_duration = total_duration - trim_start - trim_end
        if new_duration <= 0:
            logger.error(f"File {file_path} is too short to trim")
            return None

        # Define temporary output file with proper extension
        temp_file = f"{file_path}.temp.m4a"

        # Construct ffmpeg command to trim the audio while preserving artwork
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output files without asking
            '-ss', str(trim_start),
            '-i', file_path,
            '-t', str(new_duration),
            '-c', 'copy',
            '-map', '0',  # Copy all streams (including video/artwork)
            '-map_metadata', '0',  # Copy all metadata
            '-f', 'ipod',  # Explicitly set format for m4a
            temp_file
        ]

        # Execute ffmpeg command
        process = subprocess.run(cmd, capture_output=True, text=True)
        if process.returncode != 0:
            logger.error(f"FFmpeg error: {process.stderr}")
            return None

        # Replace original file with trimmed file
        os.replace(temp_file, file_path)
        logger.info(f"Successfully trimmed {file_path}")
        return file_path

    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg error while trimming {file_path}: {e.stderr}")
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        return None
    except Exception as e:
        logger.error(f"Error trimming file {file_path}: {str(e)}")
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        return None


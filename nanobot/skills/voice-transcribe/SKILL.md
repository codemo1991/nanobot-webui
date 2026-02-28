---
name: voice-transcribe
description: "Transcribe voice/audio to text using Groq Whisper API"
short_description: "Voice to text transcription"
keywords: "voice, audio, transcription, whisper, speech to text"
category: "utilities"
metadata: {"nanobot":{"emoji":"🎤"}}
---

# Voice Transcription Skill

Receive voice/audio files and transcribe them to text using Groq Whisper API.

## When to Use

Use `voice-transcribe` tool for:

- **Voice message transcription** - Convert voice messages to text
- **Audio file transcription** - Transcribe audio files (mp3, wav, m4a, etc.)
- **Meeting recording transcription** - Convert meeting recordings to text

## Usage

The `voice_transcribe` tool handles the entire workflow:

1. Receives the audio file
2. Calls Groq Whisper API for transcription
3. Returns the transcribed text

## Parameters

| Parameter | Type   | Default | Description                        |
|-----------|--------|---------|------------------------------------|
| file_path | string | required | Path to the audio file to transcribe |

## Examples

### Transcribe a voice message

```json
{
  "file_path": "/path/to/voice message.m4a"
}
```

### Transcribe an audio file

```json
{
  "file_path": "recordings/meeting_2024.wav"
}
```

## Requirements

- Groq API key must be configured (GROQ_API_KEY environment variable)
- Supported audio formats: mp3, wav, m4a, ogg, webm, flac

## Notes

- Transcription is performed using Groq's Whisper Large V3 model
- The API provides fast transcription with a generous free tier
- Maximum file size: 25MB
- Supports multiple languages (auto-detection)

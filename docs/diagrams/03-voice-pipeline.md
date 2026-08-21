# Voice pipeline

```mermaid
flowchart LR
    MIC["Microphone<br/>push-to-talk only"]
    ENC["Encode in the browser<br/>16-bit PCM WAV"]
    UP["POST /voice/transcribe<br/>multipart"]
    DEC["Decode + resample<br/>16 kHz mono"]
    VAD{"Voice activity<br/>detected?"}
    STT["faster-whisper<br/>locally"]
    NORM["Normalise<br/>strip filler, fix case"]
    AGENT["Agent"]
    TTS["Text to speech<br/>OS voices, optional"]
    SPK["Speaker"]
    NONE["'I did not hear anything'<br/>no transcript invented"]
    OFF["'Speech is unavailable'<br/>keep typing"]

    MIC --> ENC --> UP --> DEC --> VAD
    VAD -->|no| NONE
    VAD -->|yes| STT
    STT -->|unavailable| OFF
    STT --> NORM --> AGENT
    AGENT -->|only if you asked| TTS --> SPK

    classDef guard fill:#2b2519,stroke:#d9a441,color:#e6e9ec
    classDef safe fill:#1b2b28,stroke:#5eb3a1,color:#e6e9ec
    class VAD,NONE,OFF guard
    class STT,TTS,DEC safe
```

Properties this diagram encodes:

- The microphone opens on press and every track is stopped on release, so the OS
  indicator reflects reality.
- Audio is processed in memory. The server never writes it to disk.
- Silence produces an empty transcript and a clear message, never a hallucinated
  one.
- Speech never bypasses a permission or confirmation: a transcript is just text
  entering the same graph.

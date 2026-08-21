# Tool execution lifecycle

```mermaid
flowchart TD
    CALL["ToolCall<br/>{tool_name, arguments}"]
    LOOKUP{"Registered?"}
    OBS["observability<br/>time it, audit it"]
    RATE{"Within the<br/>rate limit?"}
    VALID{"Arguments match<br/>the schema?"}
    POLICY{"Capability<br/>granted?"}
    CONFIRM{"Needs<br/>confirmation?"}
    APPROVED{"Already<br/>approved?"}
    LIMIT["clamp output"]
    TIMEOUT["wall-clock budget"]
    RETRY["retry — transient only"]
    EXEC["Provider adapter"]
    OK["ToolResult{success: true}"]
    FAIL["ToolResult{success: false, error_code}"]
    PAUSE["428 CONFIRMATION_REQUIRED<br/>+ full preview"]

    CALL --> LOOKUP
    LOOKUP -->|no| FAIL
    LOOKUP -->|yes| OBS --> RATE
    RATE -->|no| FAIL
    RATE -->|yes| VALID
    VALID -->|no| FAIL
    VALID -->|yes| POLICY
    POLICY -->|deny or prompt| FAIL
    POLICY -->|allow| CONFIRM
    CONFIRM -->|no| LIMIT
    CONFIRM -->|yes| APPROVED
    APPROVED -->|no| PAUSE
    APPROVED -->|yes| LIMIT
    LIMIT --> TIMEOUT --> RETRY --> EXEC
    EXEC --> OK
    EXEC -->|raises| FAIL

    classDef gate fill:#2b2519,stroke:#d9a441,color:#e6e9ec
    classDef bad fill:#3a2020,stroke:#d97066,color:#e6e9ec
    classDef good fill:#1b2b28,stroke:#5eb3a1,color:#e6e9ec
    class LOOKUP,RATE,VALID,POLICY,CONFIRM,APPROVED gate
    class FAIL,PAUSE bad
    class OK,EXEC good
```

Every gate runs on every call. There is no fast path, no cached decision, and no
way for a caller to skip a stage — `ToolRuntime.execute` is the only entry point
and it composes the chain itself.

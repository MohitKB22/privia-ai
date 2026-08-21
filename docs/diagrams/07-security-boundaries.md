# Security boundaries

```mermaid
flowchart TB
    subgraph trusted["TRUSTED — never derived from data"]
        POLICY["PRIVIA's policy"]
        GUARDS["The security package"]
        REG["Tool registry"]
    end

    subgraph semi["SEMI-TRUSTED — expresses intent, grants nothing"]
        USER["What you typed or said"]
    end

    subgraph untrusted["UNTRUSTED — data, never instructions"]
        WEB["Web pages"]
        MAIL["Email bodies"]
        FILE["File contents"]
        OUT["Command output"]
        MODEL["Model output"]
    end

    WRAP["wrap_untrusted()<br/>envelope + injection score"]
    PROMPT["Prompt"]
    TOOLCALL["ToolCall — structurally validated"]
    RUNTIME["Tool runtime"]
    EFFECT["Side effect"]

    WEB --> WRAP
    MAIL --> WRAP
    FILE --> WRAP
    OUT --> WRAP
    WRAP --> PROMPT
    USER --> PROMPT
    POLICY --> PROMPT
    PROMPT --> MODEL
    MODEL --> TOOLCALL
    TOOLCALL --> RUNTIME
    GUARDS --> RUNTIME
    REG --> RUNTIME
    RUNTIME --> EFFECT

    WEB -.->|"cannot reach"| EFFECT
    MODEL -.->|"cannot reach as text"| EFFECT

    classDef t fill:#1b2b28,stroke:#5eb3a1,color:#e6e9ec
    classDef s fill:#2b2519,stroke:#d9a441,color:#e6e9ec
    classDef u fill:#3a2020,stroke:#d97066,color:#e6e9ec
    class POLICY,GUARDS,REG,RUNTIME t
    class USER,PROMPT s
    class WEB,MAIL,FILE,OUT,MODEL u
```

The dotted lines are the point. Untrusted content can influence what the model
*says*. It cannot reach a side effect, because the only thing that reaches the
runtime is a validated `ToolCall`, and the runtime checks capabilities regardless
of what any text claimed.

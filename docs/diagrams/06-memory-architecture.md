# Memory architecture

```mermaid
flowchart TB
    subgraph short["Short term — this conversation"]
        MSG["messages table<br/>capped by turn count"]
    end

    subgraph long["Long term — only what you approved"]
        FACT["facts"]
        PREF["preferences"]
        PIN["pinned"]
    end

    subgraph semantic["Semantic index"]
        EMB["Embedder<br/>local hashed n-grams<br/>or Ollama"]
        VEC[("memory_vectors")]
    end

    GATE{"Storable?"}
    REFUSE["Refused<br/>credentials, card numbers,<br/>anything matching a<br/>live-credential pattern"]

    QUERY["A turn arrives"]
    HYBRID["Hybrid recall<br/>cosine similarity<br/>+ literal text match"]
    CTX["Context for this turn"]

    USER["You ask it to remember"] --> GATE
    GATE -->|no| REFUSE
    GATE -->|yes| long
    long --> EMB --> VEC

    QUERY --> HYBRID
    VEC --> HYBRID
    long --> HYBRID
    MSG --> CTX
    PIN --> CTX
    HYBRID --> CTX

    classDef bad fill:#3a2020,stroke:#d97066,color:#e6e9ec
    classDef gate fill:#2b2519,stroke:#d9a441,color:#e6e9ec
    class REFUSE bad
    class GATE gate
```

Why recall is hybrid: an embedding finds "who manages the analytics team" from
"Rahul is the project manager", but it will not reliably find "BA287". Literal
matching does exactly the opposite. Users need both, so both run and the scores
are merged.

Every record carries provenance (`user:explicit`, `run:<id>`), so the question
"why do you know that?" always has an answer.

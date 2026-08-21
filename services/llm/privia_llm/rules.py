"""The deterministic rule engine.

This is **not** a language model and does not pretend to be one. It is a
transparent, testable rule engine that PRIVIA uses to classify intent, pull out
entities and build a plan.

It matters for three reasons:

* PRIVIA is useful on a machine with no model installed and no network.
* Every automated test runs against deterministic behaviour rather than a
  sampled distribution, so the security tests actually mean something.
* When a model *is* available, this engine still supplies the entity extraction
  the model would otherwise have to guess at (dates, paths, addresses), which is
  exactly the kind of thing models get wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from privia_shared.agent import Classification, Entity, Plan, PlanStep
from privia_shared.enums import Intent

# ---------------------------------------------------------------------------
# Intent rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentRule:
    intent: Intent
    pattern: re.Pattern[str]
    weight: float
    description: str


def _r(intent: Intent, regex: str, weight: float, description: str) -> IntentRule:
    return IntentRule(intent, re.compile(regex, re.IGNORECASE), weight, description)


INTENT_RULES: tuple[IntentRule, ...] = (
    # Privacy / system - checked first because they are unambiguous commands.
    _r(
        Intent.PRIVACY_CONTROL,
        r"\b(turn|switch)\s+(off|on)\b.{0,20}\b(cloud|local|memory|telemetry)\b|"
        r"\b(disable|enable)\b.{0,20}\b(cloud|memory|telemetry)\b|"
        r"\bprivacy\s+(center|settings|status)\b|\bam i (private|offline)\b",
        0.95,
        "privacy toggle",
    ),
    _r(
        Intent.ACTIVITY_REVIEW,
        r"\bwhat (files?|did you|have you)\b.{0,30}\b(access|touch|read|do|run)\b|"
        r"\b(show|list)\b.{0,20}\b(activity|audit|history|what you did)\b|"
        r"\bwhich files?\b.{0,20}\b(did|have)\b",
        0.9,
        "activity review",
    ),
    _r(
        Intent.MEMORY_FORGET,
        r"\b(forget|delete|erase|wipe|clear)\b.{0,30}\b(everything|all)?\b.{0,20}"
        r"\b(you (remember|know)|memor(y|ies)|about me)\b",
        0.95,
        "forget memory",
    ),
    _r(
        Intent.MEMORY_SAVE,
        r"\b(remember|note that|keep in mind|don'?t forget)\b(?!.{0,20}\bwhat\b)",
        0.8,
        "save memory",
    ),
    _r(
        Intent.MEMORY_RECALL,
        r"\bwhat do you (remember|know) about\b|\b(recall|what did i tell you)\b",
        0.85,
        "recall memory",
    ),
    # Email
    _r(
        Intent.EMAIL_SEND,
        r"^\s*(yes,?\s*)?(please\s+)?send (it|the (email|message|draft)|that)\b|"
        r"\bgo ahead and send\b|\bsend the (email|draft|message)\b",
        0.95,
        "send email",
    ),
    _r(
        Intent.EMAIL_DRAFT,
        r"\b(draft|write|compose|prepare)\b.{0,25}\b(email|e-mail|message|reply|note)\b|"
        r"\breply to\b.{0,30}\b(email|message)\b",
        0.9,
        "draft email",
    ),
    _r(
        Intent.EMAIL_SEARCH,
        r"\b(find|search|look for|show|check|any)\b.{0,25}\b(emails?|inbox|messages? from)\b",
        0.85,
        "search email",
    ),
    # Calendar
    _r(
        Intent.CALENDAR_CANCEL,
        r"\b(cancel|delete|remove|call off)\b.{0,25}\b(meeting|event|appointment|call)\b",
        0.9,
        "cancel event",
    ),
    _r(
        Intent.CALENDAR_CREATE,
        # The negative lookahead keeps "create a note called interview prep" and
        # "create a file about the meeting" out of the calendar branch.
        r"\b(schedule|book|set up|create|add|put)\b(?!.{0,25}\b(note|file|document|reminder note)\b)"
        r".{0,30}\b(meeting|event|appointment|call|sync|lunch|interview|standup|1:1)\b",
        0.9,
        "create event",
    ),
    _r(
        Intent.CALENDAR_VIEW,
        r"\b(what'?s|show|list|do i have|any)\b.{0,30}"
        r"\b(on my calendar|meetings?|events?|schedule|agenda|appointments?)\b|"
        r"\bam i (free|busy)\b",
        0.85,
        "view calendar",
    ),
    # Notes
    _r(
        Intent.NOTE_CREATE,
        r"\b(create|make|start|add|new|take)\b.{0,20}\bnote\b|\bnote (called|titled|named)\b",
        0.93,
        "create note",
    ),
    _r(
        Intent.NOTE_UPDATE,
        r"\b(update|edit|add to|append to|change)\b.{0,20}\b(the |my )?note\b",
        0.85,
        "update note",
    ),
    _r(
        Intent.NOTE_SEARCH,
        r"\b(find|search|show|list|open|read)\b.{0,20}\bnotes?\b",
        0.8,
        "search notes",
    ),
    # Terminal
    _r(
        Intent.TERMINAL_RUN,
        r"\brun\b.{0,25}\b(the )?(tests?|test suite|build|lint|command|script|pytest|npm|make)\b|"
        r"\b(execute|run) `[^`]+`|\bnpm (run|test)\b|\bgit (status|log|diff)\b",
        0.9,
        "run command",
    ),
    # Files
    _r(
        Intent.FILE_DELETE,
        r"\b(delete|remove|trash|get rid of)\b.{0,25}\b(file|document|\.\w{2,4}\b)",
        0.9,
        "delete file",
    ),
    _r(
        Intent.FILE_WRITE,
        r"\b(create|write|save|make)\b.{0,25}\b(a |the )?(file|document|\.\w{2,4}\b)",
        0.8,
        "write file",
    ),
    _r(
        Intent.SUMMARIZE,
        r"\b(summari[sz]e|summary of|tl;?dr|give me the gist|key points)\b",
        0.9,
        "summarize",
    ),
    _r(
        Intent.FILE_READ,
        r"\b(read|open|show me|what'?s in|display)\b.{0,25}\b(file|document|\.\w{2,4}\b)",
        0.8,
        "read file",
    ),
    _r(
        Intent.FILE_SEARCH,
        r"\b(find|search for|locate|where is|look for|do i have)\b.{0,30}"
        r"\b(file|document|resume|cv|report|invoice|presentation|photo|spreadsheet|pdf|"
        r"\.\w{2,4}\b)",
        0.85,
        "search files",
    ),
    # Web
    _r(
        Intent.WEB_READ,
        r"\b(open|read|fetch|check|what'?s on)\b.{0,20}(https?://|www\.)",
        0.9,
        "read url",
    ),
    _r(
        Intent.WEB_SEARCH,
        r"\b(search|google|look up|find out)\b.{0,25}\b(online|the web|internet)\b|"
        r"^\s*(search|google|look up)\b",
        0.8,
        "web search",
    ),
    # Conversation
    _r(
        Intent.CHITCHAT,
        r"^\s*(hi|hey|hello|good (morning|afternoon|evening)|thanks?|thank you|"
        r"cheers|ok(ay)?|nice|cool|bye|goodbye)\b[\s!.?]*$",
        0.95,
        "greeting",
    ),
    _r(
        Intent.QUESTION,
        r"^\s*(what|who|when|where|why|how|can you|could you|do you|is it|are you|" r"tell me)\b",
        0.4,
        "question",
    ),
)

#: A plan step argument may reference an earlier step's output with this syntax:
#: ``${<step index>.<dotted path into the result data>}``. The agent resolves it
#: after the referenced step runs; if the reference cannot be resolved the step
#: is skipped and the user is told why, rather than the tool being called with a
#: literal placeholder.
STEP_REFERENCE_PREFIX = "${"
FIRST_FILE_REF = "${0.files.0.path}"


#: Intents that read or write the user's own data and therefore need tools.
TOOL_INTENTS = frozenset(
    {
        Intent.FILE_SEARCH,
        Intent.FILE_READ,
        Intent.FILE_WRITE,
        Intent.FILE_DELETE,
        Intent.SUMMARIZE,
        Intent.NOTE_CREATE,
        Intent.NOTE_SEARCH,
        Intent.NOTE_UPDATE,
        Intent.CALENDAR_VIEW,
        Intent.CALENDAR_CREATE,
        Intent.CALENDAR_CANCEL,
        Intent.EMAIL_SEARCH,
        Intent.EMAIL_DRAFT,
        Intent.EMAIL_SEND,
        Intent.WEB_SEARCH,
        Intent.WEB_READ,
        Intent.TERMINAL_RUN,
        Intent.MEMORY_RECALL,
        Intent.MEMORY_SAVE,
        Intent.MEMORY_FORGET,
        Intent.PRIVACY_CONTROL,
        Intent.ACTIVITY_REVIEW,
    }
)


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"https?://[^\s<>\"']+|(?<![\w.])www\.[^\s<>\"']+")
_PATH_RE = re.compile(r"(?:~|/)[\w.~/-]{2,}")
# Typographic quotes are intentional: people paste text from documents.
_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{2,120})[\"'”’]")  # noqa: RUF001
_BACKTICK_RE = re.compile(r"`([^`]{1,200})`")
_EXTENSION_RE = re.compile(
    r"\.(md|txt|pdf|docx?|xlsx?|csv|json|ya?ml|py|js|ts|tsx|jsx|html|png|jpe?g|pptx?|zip|log|toml|rs|go|java|rb|sh)\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\b|"
    r"\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?))?\b")
_DURATION_RE = re.compile(
    r"\b(?P<n>\d{1,3})\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE
)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
#: Words that follow "to"/"with" but are not people.
_NOT_NAMES = frozenset(
    {
        "me",
        "you",
        "it",
        "them",
        "him",
        "her",
        "us",
        "the",
        "a",
        "an",
        "my",
        "our",
        "everyone",
        "team",
        "all",
        "this",
        "that",
        "tomorrow",
        "today",
        "later",
    }
)


def extract_entities(text: str, *, now: datetime | None = None) -> list[Entity]:
    """Pull structured values out of an utterance."""
    now = now or datetime.now(timezone.utc)
    entities: list[Entity] = []

    for match in _EMAIL_RE.finditer(text):
        entities.append(
            Entity(
                type="email",
                value=match.group(0),
                normalized=match.group(0).lower(),
                start=match.start(),
                end=match.end(),
            )
        )
    for match in _URL_RE.finditer(text):
        raw = match.group(0).rstrip(".,);")
        normalized = raw if raw.startswith("http") else f"https://{raw}"
        entities.append(
            Entity(
                type="url",
                value=raw,
                normalized=normalized,
                start=match.start(),
                end=match.start() + len(raw),
            )
        )
    for match in _PATH_RE.finditer(text):
        entities.append(
            Entity(
                type="path",
                value=match.group(0),
                normalized=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )
    for match in _BACKTICK_RE.finditer(text):
        entities.append(
            Entity(
                type="command",
                value=match.group(1),
                normalized=match.group(1).strip(),
                start=match.start(1),
                end=match.end(1),
            )
        )
    for match in _QUOTED_RE.finditer(text):
        entities.append(
            Entity(
                type="quoted",
                value=match.group(1),
                normalized=match.group(1).strip(),
                start=match.start(1),
                end=match.end(1),
            )
        )
    for match in _EXTENSION_RE.finditer(text):
        entities.append(
            Entity(
                type="extension",
                value=match.group(0),
                normalized=match.group(0).lower(),
                start=match.start(),
                end=match.end(),
            )
        )

    when = extract_datetime(text, now=now)
    if when is not None:
        entities.append(
            Entity(
                type="datetime", value=when[1], normalized=when[0].isoformat(), confidence=when[2]
            )
        )
    duration = _DURATION_RE.search(text)
    if duration:
        count = int(duration.group("n"))
        minutes = count * (60 if duration.group("unit").lower().startswith(("hour", "hr")) else 1)
        entities.append(
            Entity(type="duration_minutes", value=duration.group(0), normalized=str(minutes))
        )

    for name in _extract_person_names(text):
        entities.append(Entity(type="person", value=name, normalized=name, confidence=0.6))

    subject = _extract_title(text)
    if subject:
        entities.append(Entity(type="title", value=subject, normalized=subject, confidence=0.6))

    return entities


def extract_datetime(
    text: str, *, now: datetime | None = None
) -> tuple[datetime, str, float] | None:
    """Resolve a natural date/time reference. Returns ``(when, matched_text, confidence)``."""
    now = now or datetime.now(timezone.utc)
    lowered = text.lower()

    iso = _ISO_DATE_RE.search(text)
    if iso:
        date_part = datetime.strptime(iso.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if iso.group(2):
            parts = [int(p) for p in iso.group(2).split(":")]
            date_part = date_part.replace(
                hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0
            )
            return date_part, iso.group(0), 0.99
        clock = _extract_time(lowered)
        return (
            (date_part.replace(hour=clock.hour, minute=clock.minute) if clock else date_part),
            iso.group(0),
            0.95,
        )

    base: datetime | None = None
    matched = ""
    if re.search(r"\bday after tomorrow\b", lowered):
        base, matched = now + timedelta(days=2), "day after tomorrow"
    elif re.search(r"\btomorrow\b", lowered):
        base, matched = now + timedelta(days=1), "tomorrow"
    elif re.search(r"\btoday\b|\btonight\b|\bthis (morning|afternoon|evening)\b", lowered):
        base, matched = now, "today"
    elif re.search(r"\byesterday\b", lowered):
        base, matched = now - timedelta(days=1), "yesterday"
    else:
        weekday_match = re.search(r"\b(next\s+|this\s+)?(" + "|".join(_WEEKDAYS) + r")\b", lowered)
        if weekday_match:
            target = _WEEKDAYS[weekday_match.group(2)]
            delta = (target - now.weekday()) % 7
            if delta == 0 or (weekday_match.group(1) and weekday_match.group(1).strip() == "next"):
                delta = delta or 7
                if (
                    weekday_match.group(1)
                    and weekday_match.group(1).strip() == "next"
                    and delta < 7
                ):
                    delta += 7
            base, matched = now + timedelta(days=delta), weekday_match.group(0)
        else:
            in_days = re.search(r"\bin\s+(\d{1,2})\s+days?\b", lowered)
            if in_days:
                base, matched = now + timedelta(days=int(in_days.group(1))), in_days.group(0)

    clock = _extract_time(lowered)
    if base is None and clock is None:
        return None
    if base is None:
        base, matched = now, ""
    if clock is not None:
        resolved = base.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        if not matched and resolved <= now:
            resolved += timedelta(days=1)
        return resolved, (matched + " " + clock.strftime("%H:%M")).strip(), 0.85
    return base.replace(hour=9, minute=0, second=0, microsecond=0), matched, 0.7


def _extract_time(lowered: str) -> time | None:
    match = _TIME_RE.search(lowered)
    if not match:
        if "noon" in lowered:
            return time(12, 0)
        if "midnight" in lowered:
            return time(0, 0)
        return None
    if match.group("hour"):
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        meridiem = (match.group("meridiem") or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        return time(hour, minute)
    return time(int(match.group("h24")), int(match.group("m24")))


def _extract_person_names(text: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(
        r"\b(?:to|with|for|from|and)\s+([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)", text
    ):
        candidate = match.group(1).strip()
        if candidate.lower() in _NOT_NAMES:
            continue
        if candidate not in names:
            names.append(candidate)
    return names[:6]


def _extract_title(text: str) -> str | None:
    match = re.search(
        r"\b(?:called|titled|named|about|regarding|re:?)\s+" r"[\"'“]?([^\"'”\n.]{2,80})[\"'”]?",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().rstrip(".,")
    quoted = _QUOTED_RE.search(text)
    return quoted.group(1).strip() if quoted else None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(text: str, *, now: datetime | None = None) -> Classification:
    """Score every rule and return the best intent with its entities."""
    cleaned = (text or "").strip()
    if not cleaned:
        return Classification(intent=Intent.UNKNOWN, confidence=0.0, rationale="Empty input.")

    scores: dict[Intent, float] = {}
    reasons: dict[Intent, str] = {}
    for rule in INTENT_RULES:
        if rule.pattern.search(cleaned) and rule.weight > scores.get(rule.intent, 0.0):
            scores[rule.intent] = rule.weight
            reasons[rule.intent] = rule.description

    entities = extract_entities(cleaned, now=now)
    types = {e.type for e in entities}

    # Entities sharpen otherwise ambiguous phrasing.
    if "url" in types:
        scores[Intent.WEB_READ] = max(scores.get(Intent.WEB_READ, 0.0), 0.75)
    if "email" in types and scores.get(Intent.EMAIL_DRAFT, 0) == 0:
        scores[Intent.EMAIL_DRAFT] = max(scores.get(Intent.EMAIL_DRAFT, 0.0), 0.5)
    if "command" in types:
        scores[Intent.TERMINAL_RUN] = max(scores.get(Intent.TERMINAL_RUN, 0.0), 0.8)
    if "extension" in types and not scores:
        scores[Intent.FILE_SEARCH] = 0.6

    if not scores:
        intent, confidence, rationale = (
            Intent.QUESTION,
            0.3,
            "No rule matched; treated as a question.",
        )
        alternatives: tuple[Intent, ...] = ()
    else:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        intent, confidence = ranked[0]
        rationale = f"Matched rule: {reasons.get(intent, 'entity signal')}."
        alternatives = tuple(i for i, _s in ranked[1:4])

    return Classification(
        intent=intent,
        confidence=round(min(confidence, 0.99), 2),
        entities=tuple(entities),
        rationale=rationale,
        alternatives=alternatives,
    )


def entity_value(classification: Classification, entity_type: str) -> str | None:
    for entity in classification.entities:
        if entity.type == entity_type:
            return entity.normalized or entity.value
    return None


def entity_values(classification: Classification, entity_type: str) -> list[str]:
    return [e.normalized or e.value for e in classification.entities if e.type == entity_type]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def build_plan(
    text: str, classification: Classification, context: dict[str, Any] | None = None
) -> Plan:
    """Turn an intent plus entities into concrete tool calls."""
    context = context or {}
    intent = classification.intent
    steps: list[PlanStep] = []

    def step(description: str, tool: str | None = None, **arguments: Any) -> None:
        index = len(steps)
        depends = (
            tuple(range(index))
            if any(
                isinstance(v, str) and v.startswith(STEP_REFERENCE_PREFIX)
                for v in arguments.values()
            )
            else ()
        )
        steps.append(
            PlanStep(
                index=index,
                description=description,
                tool_name=tool,
                arguments=arguments,
                depends_on=depends,
            )
        )

    query = _search_query(text, classification)
    title = entity_value(classification, "title")
    path = entity_value(classification, "path")
    when = entity_value(classification, "datetime")
    emails = entity_values(classification, "email")
    people = entity_values(classification, "person")
    url = entity_value(classification, "url")
    command = entity_value(classification, "command")
    duration = entity_value(classification, "duration_minutes")

    if intent is Intent.FILE_SEARCH:
        step(
            f"Search allowed folders for '{query}'",
            "files.search",
            query=query,
            extensions=_extensions(classification),
            search_contents=False,
        )
    elif intent is Intent.FILE_READ:
        if path:
            step(f"Read {path}", "files.read", path=path)
        else:
            step(f"Find the file matching '{query}'", "files.search", query=query, limit=5)
            step("Read the best match", "files.read", path=FIRST_FILE_REF)
    elif intent is Intent.SUMMARIZE:
        if path:
            step(f"Summarise {path}", "files.summarize", path=path)
        else:
            step(
                f"Find the document matching '{query}'",
                "files.search",
                query=query,
                extensions=_extensions(classification),
                limit=5,
            )
            step("Summarise the best match", "files.summarize", path=FIRST_FILE_REF)
    elif intent is Intent.FILE_WRITE:
        if path:
            step(f"Create {path}", "files.create", path=path, content=context.get("content", ""))
        else:
            step("Ask which folder and file name to use")
    elif intent is Intent.FILE_DELETE:
        if path:
            step(f"Delete {path} after confirmation", "files.delete", path=path)
        else:
            step(f"Find the file matching '{query}' first", "files.search", query=query)
    elif intent is Intent.NOTE_CREATE:
        step(
            f"Create a note titled '{title or query}'",
            "notes.create",
            title=(title or query or "New note")[:200],
            body=context.get("body", ""),
        )
    elif intent is Intent.NOTE_SEARCH:
        step(f"Search notes for '{query}'", "notes.search", query=query)
    elif intent is Intent.NOTE_UPDATE:
        step(f"Find the note matching '{query}'", "notes.search", query=query)
    elif intent is Intent.CALENDAR_VIEW:
        days = 1 if re.search(r"\btomorrow|today\b", text, re.IGNORECASE) else 7
        arguments: dict[str, Any] = {"days": days}
        if when:
            arguments = {"start": when, "days": 1}
        step(f"List events for the next {days} day(s)", "calendar.list_events", **arguments)
    elif intent is Intent.CALENDAR_CREATE:
        step(
            "Create the event after you confirm the details",
            "calendar.create_event",
            title=title or _event_title(text) or "Meeting",
            start=when or "",
            duration_minutes=int(duration) if duration else 60,
            participants=emails or people,
        )
    elif intent is Intent.CALENDAR_CANCEL:
        step(
            f"Find the event matching '{query}'",
            "calendar.search_events",
            query=query or title or "",
        )
    elif intent is Intent.EMAIL_SEARCH:
        step(f"Search the mailbox for '{query}'", "email.search", query=query)
    elif intent is Intent.EMAIL_DRAFT:
        recipients = emails or [f"{p.lower()}@example.com" for p in people[:1]]
        step(
            "Write the draft (nothing is sent)",
            "email.draft",
            to=recipients,
            subject=title or _email_subject(text) or "",
            body=context.get("body", _email_body(text)),
        )
    elif intent is Intent.EMAIL_SEND:
        draft_id = context.get("last_draft_id")
        if draft_id:
            step("Send the draft after you confirm", "email.send", draft_id=draft_id)
        else:
            step("List drafts so you can pick one", "email.list_drafts", limit=10)
    elif intent is Intent.WEB_SEARCH:
        step(f"Search the web for '{query}'", "browser.search", query=query)
    elif intent is Intent.WEB_READ:
        if url:
            step(f"Fetch {url}", "browser.open_url", url=url)
        else:
            step("Ask for the URL")
    elif intent is Intent.TERMINAL_RUN:
        cwd = context.get("workspace_root", "")
        step(
            f"Run `{command or _guess_command(text)}` after you confirm",
            "terminal.run",
            command=command or _guess_command(text),
            cwd=cwd,
        )
    elif intent is Intent.MEMORY_RECALL:
        subject = _recall_subject(text)
        step(
            (
                f"Look up what is remembered about '{subject}'"
                if subject
                else "List everything remembered"
            ),
            "memory.recall",
            query=subject,
        )
    elif intent is Intent.MEMORY_SAVE:
        step("Store the fact", "memory.remember", content=_memory_content(text))
    elif intent is Intent.MEMORY_FORGET:
        everything = bool(re.search(r"\b(everything|all)\b", text, re.IGNORECASE))
        step("Forget after you confirm the scope", "memory.forget", all_memories=everything)
    elif intent is Intent.PRIVACY_CONTROL:
        arguments = {}
        if re.search(r"\b(turn|switch)\s+off\b|\bdisable\b", text, re.IGNORECASE):
            if re.search(r"\bcloud\b", text, re.IGNORECASE):
                arguments["cloud_processing"] = False
            if re.search(r"\bmemory\b", text, re.IGNORECASE):
                arguments["memory_enabled"] = False
        elif re.search(r"\b(turn|switch)\s+on\b|\benable\b", text, re.IGNORECASE):
            if re.search(r"\bcloud\b", text, re.IGNORECASE):
                arguments["cloud_processing"] = True
            if re.search(r"\bmemory\b", text, re.IGNORECASE):
                arguments["memory_enabled"] = True
        step("Read or change the privacy settings", "system.privacy", **arguments)
    elif intent is Intent.ACTIVITY_REVIEW:
        step("Show recent activity", "system.activity", limit=25)

    if not steps:
        return Plan(
            steps=(),
            summary="Answer directly without using any tool.",
            direct_answer=None,
        )
    return Plan(steps=tuple(steps), summary=steps[0].description)


# -- planning helpers --------------------------------------------------------

_STOPWORDS = frozenset(
    {
        "find",
        "search",
        "look",
        "for",
        "the",
        "a",
        "an",
        "my",
        "me",
        "please",
        "can",
        "you",
        "show",
        "open",
        "read",
        "get",
        "give",
        "tell",
        "about",
        "in",
        "on",
        "of",
        "to",
        "and",
        "with",
        "from",
        "is",
        "are",
        "what",
        "whats",
        "where",
        "any",
        "do",
        "i",
        "have",
        "it",
        "that",
        "this",
        "there",
        "some",
        "all",
        "up",
        "out",
        "file",
        "files",
        "document",
        "documents",
        "note",
        "notes",
        "email",
        "emails",
        "message",
        "messages",
        "summarize",
        "summarise",
        "summary",
        "create",
        "make",
        "new",
        "add",
        "list",
        "check",
        "called",
        "titled",
        "named",
        "would",
        "could",
        "should",
        "like",
        "want",
    }
)


def _search_query(text: str, classification: Classification) -> str:
    quoted = entity_value(classification, "quoted") or entity_value(classification, "title")
    if quoted:
        return quoted[:120]
    tokens = re.findall(r"[\w'-]+", text.lower())
    keep = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return " ".join(keep[:6])[:120] or text.strip()[:120]


def _extensions(classification: Classification) -> list[str]:
    return sorted(set(entity_values(classification, "extension")))


def _event_title(text: str) -> str | None:
    match = re.search(
        r"\b(?:schedule|book|set up|create|add)\s+(?:a\s+|an\s+)?([\w\s]{3,60}?)"
        r"(?:\s+(?:with|at|on|for|tomorrow|today)\b|$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip().title() if match else None


def _email_subject(text: str) -> str | None:
    match = re.search(r"\b(?:saying|about|regarding|re:?)\s+(.{3,80})", text, re.IGNORECASE)
    if not match:
        return None
    subject = match.group(1).strip().rstrip(".")
    return subject[:60].capitalize()


def _email_body(text: str) -> str:
    match = re.search(
        r"\b(?:saying|telling (?:them|him|her)|that)\s+(.{3,500})", text, re.IGNORECASE
    )
    if match:
        body = match.group(1).strip().rstrip(".")
        return body[0].upper() + body[1:] + "."
    return ""


#: Pronouns that mean "the user", so "what do you remember about me" should
#: list everything rather than search for the literal word "me".
_SELF_REFERENCES = frozenset({"me", "myself", "i", "us", "everything", "anything", "all"})


def _recall_subject(text: str) -> str:
    """Extract what the user wants recalled, or '' to mean 'everything'."""
    match = re.search(
        r"\b(?:remember|know|recall)\b[^.?]*?\babout\s+([\w\s'-]{1,60})", text, re.IGNORECASE
    )
    if match:
        subject = match.group(1).strip().rstrip("?.").strip()
        if subject.lower() in _SELF_REFERENCES or not subject:
            return ""
        return subject[:120]
    if re.search(r"\bwhat (do|did) you (remember|know)\b", text, re.IGNORECASE):
        return ""
    return _search_query(text, classify(text))


def _memory_content(text: str) -> str:
    match = re.search(
        r"\b(?:remember|note that|keep in mind|don'?t forget)\s+(?:that\s+)?(.{2,400})",
        text,
        re.IGNORECASE,
    )
    return (match.group(1).strip().rstrip(".") if match else text.strip())[:400]


def _guess_command(text: str) -> str:
    lowered = text.lower()
    if "unit test" in lowered or "the tests" in lowered or "test suite" in lowered:
        return "pytest -q"
    if "lint" in lowered:
        return "ruff check ."
    if "build" in lowered:
        return "npm run build"
    if "status" in lowered:
        return "git status"
    match = re.search(r"\brun\s+(.{2,80})", text, re.IGNORECASE)
    return match.group(1).strip().rstrip(".") if match else "ls -la"

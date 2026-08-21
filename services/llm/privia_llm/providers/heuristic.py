"""The offline provider.

This satisfies :class:`~privia_llm.base.LLMProvider` without being a language
model, so the rest of PRIVIA needs no branching for "no model installed". It is
labelled honestly everywhere it surfaces: the UI shows "offline planner", not a
model name, and :meth:`health_check` says exactly what it is.

It handles PRIVIA's own structured tasks (classification, planning, response
composition) by delegating to :mod:`privia_llm.rules`, and returns a short
templated answer for anything else.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from privia_shared.domain import ModelInfo

from ..base import ChatMessage, GenerationOptions, GenerationResult, LLMProvider
from ..rules import build_plan, classify

#: Markers PRIVIA puts in its own prompts so this provider knows the task.
TASK_MARKER = "PRIVIA_TASK:"
TASK_CLASSIFY = "classify"
TASK_PLAN = "plan"
TASK_RESPOND = "respond"


class HeuristicProvider(LLMProvider):
    name = "offline-planner"
    location = "local"
    sends_data_off_device = False

    def __init__(self, model: str = "rule-engine-v1") -> None:
        super().__init__(model)

    async def generate(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> GenerationResult:
        started = time.perf_counter()
        task, user_text, payload = _read_task(messages)
        if task == TASK_CLASSIFY:
            text = json.dumps(_classify_payload(user_text))
        elif task == TASK_PLAN:
            text = json.dumps(_plan_payload(user_text, payload))
        else:
            text = compose_response(user_text, payload)
        return GenerationResult(
            text=text,
            model=self.model,
            provider=self.name,
            location=self.location,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason="stop",
        )

    async def stream(
        self, messages: Sequence[ChatMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[str]:
        result = await self.generate(messages, options)
        for chunk in _chunks(result.text, 48):
            yield chunk

    async def health_check(self) -> ModelInfo:
        return ModelInfo(
            provider=self.name,
            model=self.model,
            available=True,
            location="local",
            detail=(
                "Deterministic rule engine. Not a language model: it classifies intent and "
                "plans tool calls from patterns, and composes answers from the tool results. "
                "Install Ollama for fluent natural-language replies."
            ),
            latency_ms=0,
        )


def _read_task(messages: Sequence[ChatMessage]) -> tuple[str, str, dict[str, Any]]:
    """Find the task marker, the user's text, and any structured context."""
    task = TASK_RESPOND
    payload: dict[str, Any] = {}
    for message in messages:
        if message.role != "system":
            continue
        if TASK_MARKER in message.content:
            marker_line = next(
                (line for line in message.content.splitlines() if TASK_MARKER in line), ""
            )
            task = marker_line.split(TASK_MARKER, 1)[1].strip().split()[0].lower()
        start = message.content.find("PRIVIA_CONTEXT_JSON:")
        if start != -1:
            raw = message.content[start + len("PRIVIA_CONTEXT_JSON:") :].strip()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
    user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
    return task, user_text, payload


def _classify_payload(text: str) -> dict[str, Any]:
    result = classify(text)
    return {
        "intent": str(result.intent),
        "confidence": result.confidence,
        "rationale": result.rationale,
        "entities": [
            {"type": e.type, "value": e.value, "normalized": e.normalized or e.value}
            for e in result.entities
        ],
    }


def _plan_payload(text: str, context: dict[str, Any]) -> dict[str, Any]:
    classification = classify(text)
    plan = build_plan(text, classification, context)
    return {
        "summary": plan.summary,
        "direct_answer": plan.direct_answer,
        "steps": [
            {
                "description": s.description,
                "tool_name": s.tool_name,
                "arguments": s.arguments,
            }
            for s in plan.steps
        ],
    }


def compose_response(user_text: str, context: dict[str, Any]) -> str:
    """Build a readable answer from tool results, with no model involved."""
    results = context.get("tool_results") or []
    intent = context.get("intent", "")

    if not results:
        return _conversational(user_text, intent, context)

    lines: list[str] = []
    for entry in results:
        tool = entry.get("tool_name", "")
        data = entry.get("data")
        if not entry.get("success", False):
            lines.append(f"{_friendly(tool)} did not work: {entry.get('error', 'unknown error')}")
            continue
        lines.append(_describe(tool, data))
    body = "\n".join(line for line in lines if line)
    return body or "Done."


def _describe(tool: str, data: Any) -> str:
    if not isinstance(data, dict):
        return f"{_friendly(tool)} finished."

    if tool == "files.search":
        files = data.get("files", [])
        if not files:
            return "I did not find any matching files in the folders you have allowed."
        listing = "\n".join(f"  - {f['name']}  ({f['path']})" for f in files[:8])
        more = f"\n  ... and {len(files) - 8} more" if len(files) > 8 else ""
        return f"Found {data.get('count', len(files))} file(s):\n{listing}{more}"
    if tool == "files.read":
        text = data.get("text", "")
        preview = text[:600] + ("..." if len(text) > 600 else "")
        return f"{data.get('path', 'The file')} contains:\n\n{preview}"
    if tool == "files.summarize":
        return f"Summary of {data.get('path', 'the document')}:\n\n{data.get('summary', '')}"
    if tool == "files.list_directory":
        entries = data.get("entries", [])
        listing = "\n".join(
            f"  - {e['name']}{'/' if e.get('is_dir') else ''}" for e in entries[:15]
        )
        return f"{data.get('path')} contains {data.get('count', 0)} item(s):\n{listing}"
    if tool in ("files.create", "files.rename", "files.move"):
        return f"Saved {data.get('path', 'the file')}."
    if tool == "files.delete":
        return f"Deleted {data.get('deleted', 'the file')}."
    if tool == "files.metadata":
        return (
            f"{data.get('name')}: {data.get('size_bytes', 0):,} bytes, "
            f"modified {str(data.get('modified_at', ''))[:16]}."
        )

    if tool == "notes.create":
        return f"Created the note \"{data.get('title')}\"."
    if tool in ("notes.search",):
        notes = data.get("notes", [])
        if not notes:
            return "There are no notes matching that."
        listing = "\n".join(f"  - {n['title']}" for n in notes[:10])
        return f"Found {len(notes)} note(s):\n{listing}"
    if tool == "notes.read":
        return f"{data.get('title')}\n\n{data.get('body', '')[:800]}"
    if tool in ("notes.update", "notes.tag"):
        return f"Updated the note \"{data.get('title')}\"."
    if tool == "notes.summarize":
        return data.get("summary", "Nothing to summarise.")

    if tool in ("calendar.list_events", "calendar.search_events"):
        events = data.get("events", [])
        if not events:
            return "Your calendar is clear for that window."
        listing = "\n".join(
            f"  - {e['title']} — {str(e['start'])[:16].replace('T', ' ')}"
            + (f" ({e['location']})" if e.get("location") else "")
            for e in events[:10]
        )
        return f"{len(events)} event(s):\n{listing}"
    if tool == "calendar.create_event":
        return (
            f"Created \"{data.get('title')}\" on "
            f"{str(data.get('start', ''))[:16].replace('T', ' ')}."
        )
    if tool == "calendar.update_event":
        return f"Updated \"{data.get('title')}\"."
    if tool == "calendar.cancel_event":
        return f"Cancelled \"{data.get('title')}\"."

    if tool == "email.search":
        messages = data.get("messages", [])
        if not messages:
            return "No messages matched that search."
        listing = "\n".join(f"  - {m.get('subject') or '(no subject)'}" for m in messages[:8])
        return f"Found {len(messages)} message(s):\n{listing}"
    if tool == "email.read":
        return f"Subject: {data.get('subject')}\n\n{(data.get('body') or '')[:800]}"
    if tool in ("email.draft", "email.reply", "email.update_draft"):
        recipients = ", ".join(a.get("address", "") for a in data.get("to", []))
        return (
            f"Draft ready for {recipients}.\n"
            f"Subject: {data.get('subject') or '(no subject)'}\n\n"
            f"{data.get('body', '')}\n\n"
            'Nothing has been sent. Say "send it" and I will show you the message to confirm.'
        )
    if tool == "email.send":
        return f"Sent to {', '.join(data.get('to', []))}."
    if tool == "email.list_drafts":
        drafts = data.get("drafts", [])
        if not drafts:
            return "There are no unsent drafts."
        listing = "\n".join(
            f"  - {d.get('subject') or '(no subject)'} ({d['id']})" for d in drafts[:8]
        )
        return f"{len(drafts)} draft(s):\n{listing}"

    if tool == "browser.search":
        results = data.get("results", [])
        if not results:
            return "The web search returned nothing."
        listing = "\n".join(f"  - {r['title']}\n    {r['url']}" for r in results[:6])
        return f"Web results (untrusted, from the open internet):\n{listing}"
    if tool == "browser.open_url":
        warning = (
            "\n\nNote: that page contained text aimed at manipulating an assistant. "
            "I treated it as data only."
            if data.get("injection_flags")
            else ""
        )
        return (
            f"{data.get('title') or data.get('final_url')}\n\n{data.get('text', '')[:900]}{warning}"
        )
    if tool == "browser.inspect_url":
        verdict = "allowed" if data.get("allowed") else "blocked"
        return f"That URL is {verdict}. {data.get('reason', '')}"

    if tool == "terminal.run":
        out = (data.get("stdout") or "").strip()
        err = (data.get("stderr") or "").strip()
        status = "succeeded" if data.get("exit_code") == 0 else f"exited {data.get('exit_code')}"
        parts = [f"`{' '.join(data.get('argv', []))}` {status} in {data.get('duration_ms', 0)} ms."]
        if out:
            parts.append(f"\n{out[:1500]}")
        if err:
            parts.append(f"\nstderr:\n{err[:600]}")
        return "\n".join(parts)
    if tool == "terminal.inspect":
        verdict = "allowed" if data.get("allowed") else "not allowed"
        extra = " It needs your confirmation." if data.get("requires_confirmation") else ""
        return f"That command is {verdict}. {data.get('reason', '')}{extra}"
    if tool == "terminal.list_allowed":
        programs = ", ".join(a["program"] for a in data.get("allowed", [])[:20])
        return f"I can run these commands in your workspace: {programs}."

    if tool == "memory.recall":
        memories = data.get("memories", [])
        if not memories:
            return "I do not have anything remembered about that."
        listing = "\n".join(f"  - {m['content']}" for m in memories[:10])
        return f"Here is what I remember:\n{listing}"
    if tool == "memory.remember":
        return f"Noted: {data.get('content')}"
    if tool == "memory.forget":
        return f"Forgotten. {data.get('deleted', 0)} memory record(s) removed."

    if tool == "system.activity":
        events = data.get("events", [])
        if not events:
            return "There is no recorded activity yet."
        listing = "\n".join(
            f"  - {e['timestamp'][:19].replace('T', ' ')}  {e['action']}"
            + (f"  {e['target']}" if e.get("target") else "")
            for e in events[:15]
        )
        return f"Recent activity:\n{listing}"
    if tool == "system.privacy":
        cloud = "on" if data.get("cloud_processing_enabled") else "off"
        memory = "on" if data.get("memory_enabled") else "off"
        folders = data.get("allowed_directories") or []
        return (
            f"Cloud processing is {cloud}. Memory is {memory}. "
            f"Local model: {data.get('local_llm')}. "
            f"Allowed folders: {', '.join(folders) if folders else 'none yet'}."
        )

    return f"{_friendly(tool)} finished."


def _conversational(user_text: str, intent: str, context: dict[str, Any]) -> str:
    lowered = user_text.lower().strip()
    if intent == "chitchat" or any(
        lowered.startswith(greeting) for greeting in ("hi", "hey", "hello", "good morning")
    ):
        return (
            "Hello. I run entirely on this machine. I can search and read your files, take "
            "notes, check your calendar, draft email, run allowlisted commands and read web "
            "pages — each one only after you grant permission."
        )
    if lowered.startswith(("thanks", "thank you", "cheers")):
        return "Any time."
    if "what can you do" in lowered or lowered == "help":
        return (
            "I can:\n"
            "  - find, read and summarise files in folders you allow\n"
            "  - create and search notes\n"
            "  - show your calendar and create events (with confirmation)\n"
            "  - draft email (sending always needs your explicit approval)\n"
            "  - read public web pages\n"
            "  - run allowlisted commands in your project folders\n"
            "  - remember things you ask me to, and forget them on request\n\n"
            "Everything runs locally by default. Nothing leaves this machine unless you "
            "switch cloud processing on."
        )
    if context.get("no_model"):
        return (
            "No language model is loaded, so I answer from rules rather than generating text. "
            "I can still run every tool. Install Ollama and pull a model for conversational "
            "replies:  ollama pull llama3.1:8b"
        )
    return (
        "I did not find a tool that fits that request. Try asking me to find a file, check "
        "your calendar, draft an email, take a note, or run a command."
    )


def _friendly(tool: str) -> str:
    return tool.replace(".", " ").replace("_", " ").capitalize()


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]

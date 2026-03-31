"""
Auto-Compact — Automatic context summarization when approaching token limits.
Directly uses Claude Code's compact prompt from services/compact/prompt.ts.
"""
import logging
import re
from typing import Optional
import anthropic
from config import CompagnonConfig, get_autocompact_threshold, get_context_window

logger = logging.getLogger(__name__)

# ── The actual compact prompt from Claude Code (compact/prompt.ts) ──
COMPACT_SYSTEM_PROMPT = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like file names, full code snippets, function signatures, file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness.

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable.
4. Errors and fixes: List all errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results.
7. Pending Tasks: Outline any pending tasks explicitly asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request.
9. Optional Next Step: List the next step related to the most recent work. Include direct quotes from recent conversation.

REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block."""


def format_compact_summary(summary: str) -> str:
    """Strip <analysis> block, extract <summary> content. From Claude Code's formatCompactSummary()."""
    # Strip analysis
    result = re.sub(r'<analysis>[\s\S]*?</analysis>', '', summary)
    # Extract summary content
    match = re.search(r'<summary>([\s\S]*?)</summary>', result)
    if match:
        content = match.group(1).strip()
        result = f"Summary:\n{content}"
    # Clean whitespace
    result = re.sub(r'\n\n+', '\n\n', result)
    return result.strip()


def estimate_token_count(messages: list[dict]) -> int:
    """Rough token estimation: ~4 chars per token."""
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total_chars += len(str(block.get("text", "")))
                    total_chars += len(str(block.get("input", "")))
                    total_chars += len(str(block.get("content", "")))
        total_chars += 20  # overhead per message
    return total_chars // 4


def should_auto_compact(messages: list[dict], model: str) -> bool:
    """Check if we've exceeded the auto-compact threshold."""
    threshold = get_autocompact_threshold(model)
    current = estimate_token_count(messages)
    return current >= threshold


async def compact_conversation(
    messages: list[dict],
    config: CompagnonConfig,
    custom_instructions: str = "",
) -> tuple[list[dict], str]:
    """
    Summarize the conversation and return compacted messages.
    Returns (new_messages, summary_text).
    """
    if len(messages) < 4:
        return messages, ""

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    # Build compact prompt
    system = COMPACT_SYSTEM_PROMPT
    if custom_instructions:
        system += f"\n\nAdditional Instructions:\n{custom_instructions}"

    # Strip image blocks to save tokens (from Claude Code's stripImagesFromMessages)
    clean_messages = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            cleaned = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("image", "document"):
                    cleaned.append({"type": "text", "text": f"[{block.get('type', 'media')}]"})
                else:
                    cleaned.append(block)
            clean_messages.append({**msg, "content": cleaned})
        else:
            clean_messages.append(msg)

    try:
        response = client.messages.create(
            model=config.model,
            max_tokens=4096,
            system=system,
            messages=clean_messages,
            temperature=0.0,
        )

        raw_summary = ""
        for block in response.content:
            if hasattr(block, 'text'):
                raw_summary += block.text

        summary = format_compact_summary(raw_summary)

        # Build new message list: summary as context + keep last 2 exchanges
        # (from Claude Code's buildPostCompactMessages pattern)
        compacted = [
            {
                "role": "user",
                "content": (
                    "This session is being continued from a previous conversation that ran "
                    "out of context. The summary below covers the earlier portion.\n\n"
                    f"{summary}\n\n"
                    "Continue from where we left off."
                ),
            },
            {
                "role": "assistant",
                "content": "Understood. I have the full context from the summary. Let's continue.",
            },
        ]

        # Preserve the last few messages for continuity
        keep_last = min(6, len(messages))
        if keep_last > 0:
            compacted.extend(messages[-keep_last:])

        logger.info(
            f"Auto-compact: {len(messages)} messages → {len(compacted)} "
            f"(~{estimate_token_count(messages)} → ~{estimate_token_count(compacted)} tokens)"
        )

        return compacted, summary

    except Exception as e:
        logger.error(f"Auto-compact failed: {e}")
        return messages, ""

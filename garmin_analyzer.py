"""Analyze Garmin CSV exports using Claude API with Tool Use.

Tool Use guarantees a valid JSON structure — Claude is forced to populate
the defined schema rather than returning free-form text, eliminating any
risk of markdown fences, stray commentary, or malformed JSON.
"""
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

PROMPT_PATH = Path(__file__).parent / "prompts" / "garmin_analyzer_prompt.txt"

# Tool definition — Claude MUST call this with the extracted activities.
# The schema is enforced by the API; invalid types cause a 400 before we ever see them.
SAVE_ACTIVITIES_TOOL = {
    "name": "save_activities",
    "description": (
        "Save all parsed Garmin activities as structured data. "
        "Call this exactly once after processing the entire CSV."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "activities": {
                "type": "array",
                "description": "List of activity objects, one per valid CSV row.",
                "items": {
                    "type": "object",
                    "properties": {
                        "activity_date": {
                            "type": "string",
                            "description": "ISO 8601 date, YYYY-MM-DD",
                        },
                        "activity_type": {
                            "type": "string",
                            "description": "Normalized English type, e.g. Running, Cycling",
                        },
                        "duration_min": {
                            "type": "number",
                            "description": "Total minutes, 2 decimal places",
                        },
                        "distance_km": {
                            "type": "number",
                            "description": "Distance in km, 3 decimal places; 0.0 if blank",
                        },
                        "avg_heart_rate": {
                            "type": ["integer", "null"],
                            "description": "Null if missing or zero",
                        },
                        "max_heart_rate": {
                            "type": ["integer", "null"],
                            "description": "Null if missing or zero",
                        },
                        "calories": {
                            "type": ["integer", "null"],
                            "description": "Null if missing or zero",
                        },
                        "elevation_gain_m": {
                            "type": "number",
                            "description": "Meters, 0.0 if blank",
                        },
                        "avg_pace_per_km": {
                            "type": ["string", "null"],
                            "description": "MM:SS per km; null for non-pace activities",
                        },
                    },
                    "required": [
                        "activity_date",
                        "activity_type",
                        "duration_min",
                        "distance_km",
                        "avg_heart_rate",
                        "max_heart_rate",
                        "calories",
                        "elevation_gain_m",
                        "avg_pace_per_km",
                    ],
                },
            }
        },
        "required": ["activities"],
    },
}


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def analyze_csv(csv_content: str) -> list[dict]:
    """Send CSV to Claude and return a list of normalized activity dicts.

    Claude is forced to call `save_activities` via tool_choice="any", which
    guarantees the response is schema-validated JSON — no fences, no prose.
    """
    system_prompt = load_system_prompt()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                # Prompt caching: system prompt is stable across requests.
                # Requires 1024+ tokens — the prompt includes few-shot examples
                # to reliably exceed this threshold.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[SAVE_ACTIVITIES_TOOL],
        # Force Claude to call the tool rather than returning a text response.
        tool_choice={"type": "any"},
        messages=[
            {
                "role": "user",
                "content": csv_content,
            }
        ],
    )

    # Extract the tool call result — guaranteed to be present because tool_choice="any"
    for block in response.content:
        if block.type == "tool_use" and block.name == "save_activities":
            return block.input["activities"]

    # Defensive fallback: should never reach here with tool_choice="any"
    raise ValueError(
        "Claude did not call save_activities. "
        f"Stop reason: {response.stop_reason}. Content: {response.content}"
    )

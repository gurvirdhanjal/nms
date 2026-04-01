"""
Gemini Layer-2 PDF narrative enhancer.

Takes the rule-based narrative dict produced by ReportNarrativeService and
rewrites the free-text prose fields (section_intro, risk_summary, interpretation)
into polished executive language.

Architecture:
  Layer 1 (MANDATORY): Rule-based narrative from ReportNarrativeService.
      Always runs. Always used as the fallback.
  Layer 2 (OPTIONAL): Gemini rewrites the free-text fields.
      Only runs when GEMINI_API_KEY is set.
      Never produces NEW findings or mentions device names / IPs.
      Cached in-memory by content hash. Validates output before applying.

Follows the same guard contract as report_insight_engine.enhance_with_gemini():
  - Any exception → return original narrative unchanged.
  - Response validation before applying to ensure structure safety.
  - insight_source field updated to "gemini_enhanced" on success.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# In-memory cache: section_hash → enhanced narrative dict
_pdf_narrative_cache: Dict[str, dict] = {}

# Fields that contain free prose — safe to rewrite
_PROSE_FIELDS = ("section_intro", "risk_summary", "interpretation")

# Maximum characters for any single enhanced text field
_MAX_FIELD_LEN = 600


def enhance_pdf_narratives(narratives: dict, report_data: dict) -> dict:
    """Enhance all section narratives in a single pass.

    Args:
        narratives: dict keyed by section name (e.g. "executive", "server_fleet",
                    "tracked_fleet") — each value is a narrative dict.
        report_data: The full report dict (used for metric context in prompts).

    Returns:
        New dict with the same keys; prose fields replaced with Gemini output
        where successful, original text preserved on any failure.
    """
    import os
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.debug("[GeminiPDF] GEMINI_API_KEY not set — skipping PDF narrative enhancement")
        return narratives

    enhanced: Dict[str, Any] = {}
    for section, narrative in narratives.items():
        if not isinstance(narrative, dict):
            enhanced[section] = narrative
            continue
        enhanced[section] = _enhance_section(section, narrative, report_data, api_key)
    return enhanced


def _enhance_section(section: str, narrative: dict, report_data: dict, api_key: str) -> dict:
    """Enhance a single section's narrative. Returns original on any failure."""
    # Skip sections with no prose to improve
    if not any(narrative.get(f) for f in _PROSE_FIELDS):
        return narrative

    cache_key = _narrative_hash(section, narrative)
    if cache_key in _pdf_narrative_cache:
        logger.debug("[GeminiPDF] Cache hit: section=%s hash=%s", section, cache_key[:8])
        return _pdf_narrative_cache[cache_key]

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = _build_prompt(section, narrative, report_data)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=400,
                temperature=0.3,
            ),
            request_options={"timeout": 10},
        )

        result = _apply_response(response.text, narrative)
        _pdf_narrative_cache[cache_key] = result
        logger.debug("[GeminiPDF] Enhanced section=%s", section)
        return result

    except Exception as exc:
        logger.warning("[GeminiPDF] Enhancement failed for section=%s: %s", section, exc)
        return narrative


def _build_prompt(section: str, narrative: dict, report_data: dict) -> str:
    """Build a grounded, safety-constrained Gemini prompt.

    Never includes device names or IP addresses — only aggregate metrics.
    """
    summary = report_data.get("summary", {})
    context_lines = []

    section_labels = {
        "executive":     "Executive Summary",
        "server_fleet":  "Server Fleet",
        "tracked_fleet": "Employee Device Fleet",
    }
    label = section_labels.get(section, section.replace("_", " ").title())

    # Aggregate metric context (safe — no device identifiers)
    uptime = summary.get("fleet_avg_uptime")
    if uptime is not None:
        context_lines.append(f"Fleet avg uptime: {uptime:.2f}%")
    total = summary.get("total_devices")
    if total:
        context_lines.append(f"Total devices: {total}")
    offline = summary.get("offline_count") or _count_offline(report_data, section)
    if offline:
        context_lines.append(f"Offline devices: {offline}")

    context_block = "\n".join(context_lines) if context_lines else "No additional context."

    # Collect existing prose to rewrite
    current_prose = {}
    for field in _PROSE_FIELDS:
        val = narrative.get(field)
        if val and isinstance(val, str) and val.strip():
            current_prose[field] = val.strip()

    prose_json = json.dumps(current_prose, ensure_ascii=False)

    return (
        f"You are a senior network operations analyst writing for an executive audience.\n"
        f"Section: {label}\n\n"
        f"Metric context (aggregate only — do NOT include in output):\n{context_block}\n\n"
        f"Rewrite the following monitoring report prose fields into concise, professional "
        f"executive language. Rules:\n"
        f"  - Maximum 2-3 sentences per field.\n"
        f"  - Do NOT mention device names, hostnames, or IP addresses.\n"
        f"  - Do NOT add new findings beyond what the input contains.\n"
        f"  - Focus on business impact and urgency.\n"
        f"  - If a field is empty or missing, omit it from the output.\n\n"
        f"Input fields (JSON):\n{prose_json}\n\n"
        f"Respond with ONLY a JSON object with the same keys, e.g.:\n"
        f'{{"section_intro": "...", "risk_summary": "..."}}'
    )


def _apply_response(response_text: str, original: dict) -> dict:
    """Validate and apply Gemini response. Returns original on any schema violation."""
    try:
        data = json.loads(response_text)
        if not isinstance(data, dict):
            return original

        result = dict(original)
        applied = False

        for field in _PROSE_FIELDS:
            if field not in data:
                continue
            val = data[field]
            if not isinstance(val, str):
                continue
            val = val.strip()
            if not val or len(val) > _MAX_FIELD_LEN:
                continue
            result[field] = val
            applied = True

        if applied:
            result["insight_source"] = "gemini_enhanced"

        return result

    except (json.JSONDecodeError, TypeError, KeyError):
        return original


def _narrative_hash(section: str, narrative: dict) -> str:
    """Cache key: SHA-256 of section name + prose field content."""
    prose = {f: narrative.get(f, "") for f in _PROSE_FIELDS}
    raw = section + json.dumps(prose, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _count_offline(report_data: dict, section: str) -> int:
    """Extract offline count from the appropriate row set."""
    if section == "server_fleet":
        rows = report_data.get("server_rows", [])
    elif section == "tracked_fleet":
        rows = report_data.get("tracked_rows", [])
    else:
        rows = report_data.get("server_rows", []) + report_data.get("tracked_rows", [])
    return sum(
        1 for r in rows
        if (r.get("availability_status") or "").lower() == "offline"
        or (r.get("uptime_pct") or 100.0) < 50.0
    )

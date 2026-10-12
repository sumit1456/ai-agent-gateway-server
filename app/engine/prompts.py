"""Compose built-in role prompts with per-agent preset and instruction layers."""
from app.engine.prompt_templates import ROLE_PROMPT_PRESETS, ROLE_PROMPT_TEMPLATES

# Compatibility aliases used by the prompt reference endpoint and older imports.
ROUTER_SYSTEM = ROLE_PROMPT_TEMPLATES["router"]["prompt"]
PLANNER_SYSTEM = ROLE_PROMPT_TEMPLATES["planner"]["prompt"]
EXECUTOR_SYSTEM = ROLE_PROMPT_TEMPLATES["executor"]["prompt"]
CONTROLLER_SYSTEM = ROLE_PROMPT_TEMPLATES["controller"]["prompt"]
REVIEWER_SYSTEM = ROLE_PROMPT_TEMPLATES["reviewer"]["prompt"]
DIRECT_SYSTEM = ROLE_PROMPT_TEMPLATES["direct"]["prompt"]
FINALIZER_SYSTEM = ROLE_PROMPT_TEMPLATES["finalizer"]["prompt"]

LEGACY_PROMPT_FIELDS = {
    "router": "router_prompt",
    "planner": "planner_prompt",
    "executor": "executor_prompt",
    "controller": "controller_prompt",
    "reviewer": "reviewer_prompt",
    "direct": "direct_prompt",
    "finalizer": "finalizer_prompt",
}


def _fill_known_variables(prompt: str, values: dict) -> str:
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


def get_prompt(config, prompt_type: str, **format_kwargs) -> str:
    """Return role defaults plus selected preset and additive user instructions.

    Legacy `*_prompt` values remain full overrides for compatibility and for
    the dashboard's explicit advanced override control.
    """
    role = ROLE_PROMPT_TEMPLATES.get(prompt_type)
    if not role:
        return ""

    legacy = getattr(config, LEGACY_PROMPT_FIELDS[prompt_type], None)
    if legacy:
        return _fill_known_variables(legacy, format_kwargs)

    base = _fill_known_variables(role["prompt"], format_kwargs)
    preset_id = (getattr(config, "role_prompt_presets", {}) or {}).get(prompt_type, "balanced")
    presets = ROLE_PROMPT_PRESETS.get(prompt_type, [])
    preset = next((item for item in presets if item["id"] == preset_id), None)
    if preset is None:
        preset = next((item for item in presets if item["id"] == "balanced"), None)

    additions = []
    if preset and preset.get("instructions"):
        additions.append(f"Selected behavior preset ({preset['name']}):\n{preset['instructions']}")
    user_instruction = (getattr(config, "role_instructions", {}) or {}).get(prompt_type, "").strip()
    if user_instruction:
        additions.append(f"Additional user instructions for this role:\n{user_instruction}")
    return "\n\n".join([base, *additions])

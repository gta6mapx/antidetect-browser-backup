"""RPA (recipe-driven browser automation) package — Phase 7.

Adapted from python_rpa_ui (https://github.com/anandhu1228/python_rpa_ui,
Apache-2.0); see ``vendor/rpa-studio/ATTRIBUTION.md``.
"""

from src.rpa.manager import RPAManager
from src.rpa.recipes import (
    Recipe,
    FlowStep,
    DelayConfig,
    create_recipe,
    get_recipe,
    list_recipes,
    update_recipe,
    delete_recipe,
)
from src.rpa.inspector import inspect_page, inspect_url
from src.rpa import engine

__all__ = [
    "RPAManager",
    "Recipe",
    "FlowStep",
    "DelayConfig",
    "create_recipe",
    "get_recipe",
    "list_recipes",
    "update_recipe",
    "delete_recipe",
    "inspect_page",
    "inspect_url",
    "engine",
]

"""Supported plugin boundary. Importing this module initializes no services."""

from typing import Any

from .core import (
    Action,
    ActionContext,
    ActionRef,
    Actions,
    AppError,
    BaseEvents,
    CommandField,
    Commands,
    CommandSpec,
    Completions,
    Components,
    Events,
    Features,
    NamespaceSpec,
    Plugin,
    PluginContext,
    Private,
    PrivateHandle,
    Records,
    Registration,
    ResourceScope,
    SecretValue,
    StateSpec,
    Window,
    WindowContext,
    Windows,
    Workers,
    action,
    action_id,
    contract,
    lazy_imgui,
    on,
)

__all__ = [
    "Action",
    "ActionContext",
    "ActionRef",
    "Actions",
    "AppError",
    "BaseEvents",
    "Components",
    "CommandField",
    "CommandSpec",
    "Commands",
    "Completions",
    "Events",
    "Features",
    "NamespaceSpec",
    "Plugin",
    "PluginContext",
    "Private",
    "PrivateHandle",
    "Registration",
    "Records",
    "ResourceScope",
    "SecretValue",
    "StateSpec",
    "Window",
    "WindowContext",
    "Windows",
    "Workers",
    "action",
    "action_id",
    "contract",
    "on",
    "imgui_bundle",
]


def __getattr__(name: str) -> Any:
    if name == "imgui_bundle":
        return lazy_imgui()
    raise AttributeError(name)

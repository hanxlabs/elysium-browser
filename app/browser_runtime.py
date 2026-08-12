"""Shared CloakBrowser context construction helpers."""

from collections.abc import Callable
from typing import Protocol


class BrowserRuntimeSettings(Protocol):
    """The gateway settings required to launch an isolated browser context."""

    headless: bool
    humanize: bool
    human_preset: str


def launch_isolated_context(
    settings: BrowserRuntimeSettings,
    context_factory: Callable[..., object] | None = None,
    *,
    force_headed: bool = False,
) -> object:
    """Create one request-scoped context with the existing launch options."""
    factory = context_factory
    if factory is None:
        from cloakbrowser import launch_context

        factory = launch_context
    return factory(
        headless=False if force_headed else settings.headless,
        humanize=settings.humanize,
        human_preset=settings.human_preset,
    )

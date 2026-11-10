"""Application entry: native GUI on main thread, asyncio on one runtime thread."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable

from mkapp.sdk import AppError
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost

from .config import AppConfig, preferences
from .host import Host


class Runtime:
    def __init__(
        self, config: AppConfig, windows: WindowHost, *, can_restart: bool = False
    ) -> None:
        self.config, self.windows = config, windows
        self.startup: concurrent.futures.Future[None] = concurrent.futures.Future()
        self.finished: concurrent.futures.Future[None] = concurrent.futures.Future()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.host: Host | None = None
        self.shutdown: asyncio.Event | None = None
        self.can_restart = can_restart
        self.stop_requested = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="mkapp-runtime", daemon=True
        )

    def _run(self) -> None:
        async def main() -> None:
            self.loop = asyncio.get_running_loop()
            self.shutdown = asyncio.Event()
            self.host = Host(self.config, self.windows)
            self.host.can_restart = self.can_restart
            try:
                if self.stop_requested.is_set():
                    self.shutdown.set()
                startup = asyncio.create_task(self.host.start())
                shutdown = asyncio.create_task(self.shutdown.wait())
                done, _ = await asyncio.wait(
                    {startup, shutdown}, return_when=asyncio.FIRST_COMPLETED
                )
                if startup not in done:
                    startup.cancel()
                    await asyncio.wait(
                        {startup}, timeout=self.config.limits.lifecycle_seconds
                    )
                else:
                    await startup
                self.startup.set_result(None)
                await self.shutdown.wait()
            except BaseException:
                if not self.startup.done():
                    self.startup.set_exception(AppError("startup_failed"))
            finally:
                await self.host.stop()
                if self.host.shutdown_errors:
                    raise AppError("shutdown_failed")

        try:
            asyncio.run(main())
            self.finished.set_result(None)
        except BaseException:
            self.finished.set_exception(AppError("shutdown_failed"))

    def request_stop(self) -> None:
        self.stop_requested.set()
        if self.loop and self.shutdown:
            self.loop.call_soon_threadsafe(self.shutdown.set)


def run(
    config: AppConfig,
    *,
    smoke_frames: int | None = None,
    relaunch: Callable[[], None] | None = None,
) -> None:
    """Run the desktop app on the main thread. smoke_frames is for native checks."""
    if threading.current_thread() is not threading.main_thread():
        raise AppError("permission_denied")
    config = preferences(config, config.data_dir / "preferences.json")
    from imgui_bundle import hello_imgui, imgui

    bridge = Bridge(config.limits)
    windows = WindowHost(bridge, tiles="tiles" in config.extensions)
    runtime = Runtime(config, windows, can_restart=relaunch is not None)
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = config.title
    params.app_window_params.restore_previous_geometry = False
    params.ini_disable = True
    params.imgui_window_params.show_menu_bar = False
    params.imgui_window_params.show_status_bar = False
    if windows.tiles:
        params.imgui_window_params.default_imgui_window_type = (
            hello_imgui.DefaultImGuiWindowType.no_default_window
        )
    frames = 0

    def initialized() -> None:
        imgui.get_io().set_ini_filename("")
        if windows.tiles:
            imgui.get_io().config_flags |= imgui.ConfigFlags_.docking_enable

    def render() -> None:
        nonlocal frames
        windows.render(imgui)
        if runtime.startup.done():
            try:
                runtime.startup.result()
            except AppError:
                params.app_shall_exit = True
        frames += 1
        if runtime.host and runtime.host.restart_requested:
            params.app_shall_exit = True
        if smoke_frames is not None and frames >= smoke_frames:
            params.app_shall_exit = True

    def closing() -> None:
        windows.close_all()
        bridge.closed = True
        runtime.request_stop()

    params.callbacks.post_init = initialized
    params.callbacks.show_gui = render
    params.callbacks.before_exit = closing
    runtime.thread.start()
    try:
        hello_imgui.run(params)
    finally:
        closing()
        # Outside render callbacks; the GUI no longer owns resources needed by stop.
        runtime.thread.join(timeout=config.limits.lifecycle_seconds * 3)
        if runtime.thread.is_alive():
            raise AppError("shutdown_timeout")
        runtime.finished.result()
        runtime.startup.result()
        if runtime.host and runtime.host.restart_requested and relaunch is not None:
            try:
                relaunch()
            except Exception:
                raise AppError("relaunch_failed") from None


def main() -> None:
    from mkapp.cli import main as cli

    cli()

from krita import Krita

from . import debug

debug.configure()

from .docker import WindowCaptureDocker
from .window_capture_layer import WindowCaptureExtension

log = debug.logger

DOCKER_ID = "window_capture_layer_docker"

try:
    Krita.instance().addExtension(WindowCaptureExtension(Krita.instance()))
except Exception as exc:
    log.warning("failed to register extension: %s", exc)

try:
    from krita import DockWidgetFactory, DockWidgetFactoryBase

    Krita.instance().addDockWidgetFactory(
        DockWidgetFactory(
            DOCKER_ID,
            DockWidgetFactoryBase.DockRight,
            WindowCaptureDocker,
        )
    )
except Exception as exc:
    log.warning("failed to register docker: %s", exc)

"""One capture layer that receives window frames.

Needs krita at runtime. Qt is only imported for QByteArray via qt_compat so
the module still imports in tests without PyQt.

``create()`` attaches a transform mask so the capture layer can be moved and
scaled non-destructively. Pixel writes only touch the paint layer, never the
mask.
"""


import time

from . import debug
from .qt_compat import QByteArray

log = debug.logger


class LayerTarget:
    """Wraps one paint layer that receives window frames."""

    def __init__(self, doc, name, node=None):
        self.doc = doc
        self._name = name
        self._node = node
        self._last_size = None

    @classmethod
    def attach(cls, doc, node):
        target = cls(doc, node.name())
        target._node = node
        return target

    # -- identity ----------------------------------------------------------

    @property
    def uuid(self):
        if self._node is None:
            return ""
        try:
            return str(self._node.uniqueId())
        except Exception:
            return ""

    @property
    def name(self):
        if self._node is None:
            return self._name
        try:
            return self._node.name()
        except Exception:
            return self._name

    # -- lifecycle ---------------------------------------------------------

    def create(self):
        """Add a new paint layer above the current top layer."""
        try:
            node = self.doc.createNode(self._name, "paintLayer")
        except Exception as exc:
            log.warning("createNode failed: %s", exc)
            return False
        if node is None:
            log.warning("createNode returned no node for %r", self._name)
            return False
        try:
            self.doc.rootNode().addChildNode(node, None)
        except Exception as exc:
            log.warning("addChildNode failed: %s", exc)
            return False
        self._node = node
        self._attach_transform_mask(node)
        log.info("created layer %r uuid=%s", self.name, self.uuid)
        return True

    def _attach_transform_mask(self, node):
        try:
            mask = self.doc.createTransformMask("Transform Mask")
        except Exception as exc:
            log.warning("createTransformMask failed: %s", exc)
            return False
        if mask is None:
            log.warning("createTransformMask returned no mask")
            return False
        try:
            attached = node.addChildNode(mask, None)
        except Exception as exc:
            log.warning("could not attach transform mask: %s", exc)
            return False
        if not attached:
            log.warning("addChildNode refused transform mask")
            return False
        return True

    def alive(self):
        if self._node is None:
            return False
        try:
            return self.doc.nodeByUniqueID(self._node.uniqueId()) is not None
        except Exception:
            try:
                return self._node.parentNode() is not None
            except Exception:
                return False

    def delete(self):
        """Remove this layer from the document."""
        if self._node is None:
            return False
        try:
            removed = bool(self._node.remove())
        except Exception as exc:
            log.warning("remove layer failed: %s", exc)
            return False
        if removed:
            log.info("deleted layer %r", self._name)
            self._node = None
        return removed

    # -- pixels ------------------------------------------------------------

    def write(self, image):
        """Write a full-frame image into the layer.

        Frames go to the image origin, so the capture layer must stay at
        position (0, 0). Move or scale it with the attached transform mask,
        not the Move tool. Needs manual test M2.
        """
        if self._node is None or image is None:
            return False

        started = time.perf_counter()
        try:
            # Clear the old rectangle first when the new frame is smaller,
            # otherwise stale pixels stay behind.
            if self._last_size is not None:
                old_w, old_h = self._last_size
                if old_w > image.w or old_h > image.h:
                    log.debug(
                        "clearing stale %dx%d before %dx%d",
                        old_w,
                        old_h,
                        image.w,
                        image.h,
                    )
                    zeros = QByteArray(b"\x00" * (old_w * old_h * 4))
                    self._node.setPixelData(zeros, 0, 0, old_w, old_h)

            written = self._node.setPixelData(
                QByteArray(image.data), 0, 0, image.w, image.h
            )
        except Exception as exc:
            log.warning("setPixelData failed: %s", exc)
            return False

        if not written:
            log.warning("setPixelData refused %dx%d write", image.w, image.h)
            return False

        self._last_size = (image.w, image.h)
        log.debug(
            "wrote %dx%d to %r in %.1fms",
            image.w,
            image.h,
            self.name,
            (time.perf_counter() - started) * 1000,
        )
        try:
            self.doc.setModified(True)
        except Exception:
            pass
        return True

"""Session manager: captures running sessions once when Krita gains focus.

Qt is imported lazily so this module stays importable in tests without PyQt.
"""

import time
import weakref
from hashlib import blake2b

from . import backend, debug, limits, session as session_mod
from .layer_target import LayerTarget
from .qt_compat import APPLICATION_ACTIVE, QByteArray, QGuiApplication, QUuid

log = debug.logger

State = session_mod.State
RUNNING = session_mod.RUNNING
CaptureSession = session_mod.CaptureSession

SETTINGS_GROUP = "window_capture_layer"


class _Runtime:
    """In-memory state that is not persisted."""

    def __init__(self, capture_session, doc, target):
        self.session = capture_session
        self.doc = doc
        self.doc_key = document_key(doc) if doc is not None else ("", "", "")
        self.target = target
        self.last_digest = b""
        self.last_size = (0, 0)


class SessionManager:

    def __init__(self, app=None, grabber=None, target_factory=None):
        self._app = app
        self._grab = grabber
        self._target_factory = target_factory or LayerTarget
        self._runtimes = {}
        self._focus_ready = False
        self._notifier_ready = False
        self._restored_keys = set()
        self.on_refresh = None

    # -- app / settings ----------------------------------------------------

    def app(self):
        if self._app is None:
            from krita import Krita

            self._app = Krita.instance()
        return self._app

    def _connect_notifier(self):
        if self._notifier_ready:
            return
        self._notifier_ready = True
        try:
            notifier = self.app().notifier()
        except Exception as exc:
            log.warning("no notifier: %s", exc)
            return
        for signal, slot in (
            (getattr(notifier, "applicationClosing", None), self.pause_all),
            (getattr(notifier, "imageClosed", None), self._on_image_closed),
        ):
            try:
                if signal is not None:
                    signal.connect(slot)
            except Exception as exc:
                log.warning("could not connect notifier: %s", exc)

    def _connect_focus(self):
        """Capture once whenever Krita becomes the active application."""
        if self._focus_ready:
            return
        try:
            app = QGuiApplication.instance()
        except Exception:
            app = None
        if app is None:
            return
        self._focus_ready = True
        try:
            app.applicationStateChanged.connect(self._on_application_state_changed)
        except Exception as exc:
            log.warning("no focus hook: %s", exc)

    def _on_application_state_changed(self, state):
        try:
            active = state == APPLICATION_ACTIVE
        except Exception:
            return
        if active:
            log.debug("krita became active: refreshing running sessions")
            self.refresh_active()

    def refresh_active(self):
        """Grab one frame for every running session now.

        Used on focus-in and when a session starts while Krita is already
        active. There is no periodic live timer; frames update on focus only.
        """
        self.prune_dead()
        runtimes = [
            runtime
            for runtime in self._runtimes.values()
            if runtime.session.state in RUNNING
        ]
        self._capture_all(runtimes)
        self._notify_refresh()
        return len(runtimes)

    def capture(self, session_id):
        """Grab one frame for *session_id* now, even while it is paused.

        A paused session stays paused; the manual refresh is a one-shot.
        Returns the number of sessions attempted (0 or 1).
        """
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return 0
        paused = runtime.session.state == State.PAUSED
        self._capture_all([runtime])
        if paused:
            runtime.session.state = State.PAUSED
            self._save(runtime.doc)
        self._notify_refresh()
        return 1

    def capture_all(self, doc=None):
        """Grab one frame for every session now, including paused ones.

        Pass *doc* to limit the burst to one document. Paused sessions stay
        paused. Returns the number of sessions attempted.
        """
        self.prune_dead(doc)
        key = document_key(doc) if doc is not None else None
        runtimes = [
            runtime
            for runtime in self._runtimes.values()
            if key is None or runtime.doc_key == key
        ]
        paused = [r for r in runtimes if r.session.state == State.PAUSED]
        self._capture_all(runtimes)
        for runtime in paused:
            runtime.session.state = State.PAUSED
            self._save(runtime.doc)
        self._notify_refresh()
        return len(runtimes)

    def _notify_refresh(self):
        callback = self.on_refresh
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            log.warning("refresh callback failed: %s", exc)

    def _read_setting(self, name, default=""):
        try:
            return self.app().readSetting(SETTINGS_GROUP, name, str(default))
        except Exception:
            return default

    def _write_setting(self, name, value):
        try:
            self.app().writeSetting(SETTINGS_GROUP, name, str(value))
        except Exception:
            pass

    def default_interval(self):
        raw = self._read_setting("interval_ms", limits.DEFAULT_INTERVAL_MS)
        return session_mod.clamp_interval(raw)

    def last_title(self):
        return self._read_setting("last_title", "")

    def remember_title(self, title):
        self._write_setting("last_title", title)

    # -- queries -----------------------------------------------------------

    def snapshot(self, doc=None):
        """Plain dicts for the docker; safe to call every UI tick.

        Pass *doc* to show only sessions stored for that document.
        """
        key = document_key(doc) if doc is not None else None
        rows = []
        for runtime in self._runtimes.values():
            if key is not None and runtime.doc_key != key:
                continue
            capture_session = runtime.session
            rows.append(
                {
                    "id": capture_session.id,
                    "title": capture_session.title,
                    "layer_name": capture_session.layer_name,
                    "state": State(capture_session.state).value,
                    "message": capture_session.message,
                    "size": runtime.last_size,
                }
            )
        rows.sort(key=lambda row: row["id"])
        return rows

    def prune_dead(self, doc=None):
        """Drop sessions whose layer was deleted; relink renamed layers.

        Returns the number of sessions removed.
        """
        key = document_key(doc) if doc is not None else None
        removed = 0
        dropped_docs = {}
        for session_id, runtime in list(self._runtimes.items()):
            if key is not None and runtime.doc_key != key:
                continue
            target = runtime.target
            if target is None:
                continue
            try:
                alive = target.alive()
            except Exception:
                alive = True
            if alive:
                name = target.name
                if name and name != runtime.session.layer_name:
                    runtime.session.layer_name = name
                    dropped_docs[runtime.doc_key] = runtime.doc
                    log.info("session %s layer renamed to %r", session_id, name)
                continue
            replacement = self._relink(runtime.doc, runtime.session)
            if replacement is not None:
                runtime.target = replacement
                log.info("session %s relinked after layer change", session_id)
                continue
            self._drop(session_id)
            dropped_docs[runtime.doc_key] = runtime.doc
            removed += 1
        for doc_obj in dropped_docs.values():
            self._save(doc_obj)
        return removed

    def _drop(self, session_id):
        runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return None
        log.info(
            "session %s removed: layer %r deleted",
            session_id,
            runtime.session.layer_name,
        )
        return runtime

    def has_active(self):
        return any(
            runtime.session.state in RUNNING
            for runtime in self._runtimes.values()
        )

    # -- lifecycle ---------------------------------------------------------

    def add(self, doc, title, wid=0, interval_ms=None):
        if len(self._runtimes) >= limits.MAX_SESSIONS:
            raise ValueError(
                "session limit reached (%d)" % limits.MAX_SESSIONS
            )

        capture_session = CaptureSession(
            title=str(title),
            wid=int(wid or 0),
            interval_ms=session_mod.clamp_interval(
                interval_ms if interval_ms is not None else self.default_interval()
            ),
            state=State.ACTIVE,
        )
        runtime = _Runtime(capture_session, doc, None)

        problem = document_error(doc)
        if problem:
            capture_session.state = State.ERROR
            capture_session.message = problem
        else:
            target = self._target_factory(
                doc, "Window capture %s" % capture_session.id[:4]
            )
            try:
                created = target.create()
            except Exception as exc:
                created = False
                capture_session.message = str(exc)
            if created:
                runtime.target = target
                capture_session.layer_uuid = target.uuid
                capture_session.layer_name = target.name
            else:
                capture_session.state = State.ERROR
                if not capture_session.message:
                    capture_session.message = "could not create layer"

        self._runtimes[capture_session.id] = runtime
        self._connect_notifier()
        self._connect_focus()
        self._save(doc)
        if capture_session.state == State.ACTIVE:
            log.info(
                "session %s added: title=%r wid=%s interval=%dms layer=%r",
                capture_session.id,
                capture_session.title,
                capture_session.wid,
                capture_session.interval_ms,
                capture_session.layer_name,
            )
        else:
            log.warning(
                "session %s added but not started: %s",
                capture_session.id,
                capture_session.message,
            )
        return capture_session

    def snapshot_once(self, doc, title, wid=0):
        """Create a paused session and capture one frame immediately."""
        capture_session = self.add(doc, title, wid=wid)
        if capture_session.state != State.ACTIVE:
            return capture_session
        capture_session.state = State.PAUSED
        self._capture(self._runtimes[capture_session.id])
        self._save(doc)
        return capture_session

    def remove(self, session_id):
        runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return False
        target = runtime.target
        if target is not None:
            try:
                target.delete()
            except Exception as exc:
                log.warning("could not delete layer: %s", exc)
        log.info(
            "session %s removed (layer %r deleted)",
            session_id,
            runtime.session.layer_name,
        )
        self._save(runtime.doc)
        return True

    def detach(self, session_id):
        """Forget a session but keep its layer.

        The layer stays in the document, untouched and no longer written by
        the plugin. Dropping the runtime removes the saved entry on save, so a
        reopened document does not bring the session back.
        """
        runtime = self._runtimes.pop(session_id, None)
        if runtime is None:
            return False
        log.info(
            "session %s detached (layer %r kept)",
            session_id,
            runtime.session.layer_name,
        )
        self._save(runtime.doc)
        return True

    def pause(self, session_id):
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return False
        runtime.session.state = State.PAUSED
        runtime.session.message = ""
        log.debug("session %s paused", session_id)
        self._save(runtime.doc)
        return True

    def resume(self, session_id):
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return False
        problem = document_error(runtime.doc)
        if problem:
            runtime.session.state = State.ERROR
            runtime.session.message = problem
            log.warning("session %s resume refused: %s", session_id, problem)
        else:
            had_target = runtime.target is not None
            if runtime.target is None or not runtime.target.alive():
                runtime.target = self._relink(runtime.doc, runtime.session)
            if runtime.target is None or not runtime.target.alive():
                if had_target:
                    self._drop(session_id)
                else:
                    runtime.session.state = State.ERROR
                    runtime.session.message = "layer missing"
                    log.warning(
                        "session %s resume refused: layer missing (uuid=%r name=%r)",
                        session_id,
                        runtime.session.layer_uuid,
                        runtime.session.layer_name,
                    )
            else:
                runtime.session.state = State.ACTIVE
                runtime.session.message = ""
                log.info("session %s resumed", session_id)
        self._save(runtime.doc)
        return True

    def toggle(self, session_id):
        """Pause a running session (ACTIVE or LOST), otherwise resume it."""
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return False
        if runtime.session.state in RUNNING:
            return self.pause(session_id)
        return self.resume(session_id)

    def pause_all(self):
        for runtime in self._runtimes.values():
            if runtime.session.state in RUNNING:
                runtime.session.state = State.PAUSED

    def toggle_all(self):
        """Pause all when anything runs, otherwise start every stopped session."""
        if self.has_active():
            self.pause_all()
            return False
        for session_id in list(self._runtimes):
            self.resume(session_id)
        return self.has_active()

    def _on_image_closed(self, file_name):
        for session_id, runtime in list(self._runtimes.items()):
            if runtime.doc is not None and runtime.doc.fileName() == file_name:
                self._runtimes.pop(session_id, None)
        self._restored_keys = {
            key for key in self._restored_keys if key[1] != file_name
        }

    # -- capturing ---------------------------------------------------------

    def _capture_all(self, runtimes):
        touched = {}
        for runtime in runtimes:
            if self._capture(runtime):
                touched[runtime.doc_key] = runtime.doc
        for doc in touched.values():
            try:
                doc.refreshProjection()
            except Exception as exc:
                log.warning("refreshProjection failed: %s", exc)

    def _grab_image(self, wid, max_pixels):
        grabber = self._grab
        if grabber is None:
            from . import frame

            grabber = frame.grab_frame
        return grabber(wid, max_pixels)

    def _capture(self, runtime):
        """Grab and write one frame. Returns True when pixels were written."""
        capture_session = runtime.session
        doc = runtime.doc

        problem = document_error(doc)
        if problem:
            capture_session.state = State.ERROR
            capture_session.message = problem
            self._save(doc)
            return False

        if not self._doc_alive(doc):
            self._drop(capture_session.id)
            return False

        if not _document_idle(doc):
            log.debug("session %s skipped: document busy", capture_session.id)
            return False

        if capture_session.state == State.LOST:
            wid = backend.best_window(capture_session.title)
            if not wid:
                log.debug("session %s still lost: no window for %r", capture_session.id, capture_session.title)
                return False
            capture_session.wid = wid
            log.info("session %s retrying window %s", capture_session.id, wid)

        started = time.monotonic()
        image = self._grab_image(capture_session.wid, limits.MAX_PIXELS)
        if image is None:
            capture_session.state = State.LOST
            capture_session.message = "window not found"
            log.warning("session %s lost: window not found", capture_session.id)
            self._save(doc)
            return False

        if capture_session.state == State.LOST:
            capture_session.state = State.ACTIVE
            capture_session.message = ""
            log.info("session %s recovered", capture_session.id)

        image_digest = blake2b(image.data, digest_size=8).digest()
        if image_digest == runtime.last_digest:
            log.debug("session %s unchanged, skipped", capture_session.id)
            return False

        if runtime.target is None or not runtime.target.alive():
            runtime.target = self._relink(doc, capture_session)
            if runtime.target is None:
                self._drop(capture_session.id)
                self._save(doc)
                return False

        try:
            written = runtime.target.write(image)
        except Exception as exc:
            capture_session.state = State.ERROR
            capture_session.message = str(exc)
            log.warning("session %s write failed: %s", capture_session.id, exc)
            self._save(doc)
            return False

        if not written:
            log.debug("session %s write refused", capture_session.id)
            return False

        runtime.last_digest = image_digest
        runtime.last_size = (image.w, image.h)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.debug(
            "session %s wrote %dx%d in %dms",
            capture_session.id,
            image.w,
            image.h,
            elapsed_ms,
        )
        return True

    def _relink(self, doc, capture_session):
        node = _node_by_uuid(doc, capture_session.layer_uuid)
        if node is None and capture_session.layer_name:
            node = _node_by_name(doc, capture_session.layer_name)
            if node is not None:
                log.info(
                    "session %s relinked by name %r (stored uuid %r)",
                    capture_session.id,
                    capture_session.layer_name,
                    capture_session.layer_uuid,
                )
        if node is None:
            return None
        try:
            target = LayerTarget.attach(doc, node)
        except Exception:
            return None
        capture_session.layer_uuid = target.uuid or capture_session.layer_uuid
        capture_session.layer_name = target.name or capture_session.layer_name
        return target

    def _doc_alive(self, doc):
        try:
            documents = self.app().documents()
        except Exception:
            return True
        if documents is None:
            return True
        try:
            key = document_key(doc)
            if key is None:
                return True
            return any(document_key(d) == key for d in documents)
        except Exception:
            return True

    # -- persistence -------------------------------------------------------

    def restore(self, doc):
        """Restore stored sessions as PAUSED. Safe to call repeatedly."""
        if doc is None:
            return []
        key = document_key(doc)
        if key is None:
            return []
        if key in self._restored_keys:
            return []
        self._restored_keys.add(key)

        sessions = session_mod.from_json(_read_annotation(doc))
        restored = []
        for capture_session in sessions:
            if len(self._runtimes) >= limits.MAX_SESSIONS:
                break
            capture_session.state = State.PAUSED
            runtime = _Runtime(capture_session, doc, None)
            runtime.target = self._relink(doc, capture_session)
            if runtime.target is None:
                capture_session.message = "layer missing"
                log.warning(
                    "restored session %s has no layer (uuid=%r name=%r)",
                    capture_session.id,
                    capture_session.layer_uuid,
                    capture_session.layer_name,
                )
            restored.append(capture_session)
            self._runtimes[capture_session.id] = runtime
        if restored:
            log.info("restored %d session(s) as paused for %r", len(restored), key)
        return restored

    def _save(self, doc):
        if doc is None:
            return
        key = document_key(doc)
        if key is None:
            return
        sessions = [
            runtime.session
            for runtime in self._runtimes.values()
            if runtime.doc_key == key
        ]
        try:
            _write_annotation(doc, session_mod.to_json(sessions))
        except Exception as exc:
            log.warning("could not save sessions: %s", exc)
        else:
            log.debug("saved %d session(s) for %r", len(sessions), key)


# -- helpers ---------------------------------------------------------------


def _document_idle(doc):
    """Best-effort idle check.

    ``Document.isIdle()`` is listed in the API docs but missing from some
    Krita 5.x builds; treat a missing check as idle instead of erroring.
    """
    check = getattr(doc, "isIdle", None)
    if not callable(check):
        return True
    try:
        return bool(check())
    except Exception:
        return True


def document_error(doc):
    """Return "" when the document can take pixel writes, else a reason."""
    if doc is None:
        return "no document"
    try:
        model, depth = doc.colorModel(), doc.colorDepth()
    except Exception:
        return "document is not RGBA/U8"
    if (model, depth) == ("RGBA", "U8"):
        return ""
    return "document is %s/%s; RGBA/U8 required" % (model, depth)


def _node_by_uuid(doc, uuid_text):
    if not uuid_text:
        return None
    try:
        return doc.nodeByUniqueID(QUuid(uuid_text))
    except Exception:
        return None


def _node_by_name(doc, name):
    if not name:
        return None
    try:
        return doc.nodeByName(name)
    except Exception:
        return None


def _read_annotation(doc):
    try:
        payload = doc.annotation(session_mod.ANNOTATION_TYPE)
    except Exception:
        return ""
    if not payload:
        return ""
    try:
        return bytes(payload).decode("utf-8")
    except (TypeError, ValueError):
        return ""


def _write_annotation(doc, text):
    doc.setAnnotation(
        session_mod.ANNOTATION_TYPE,
        session_mod.ANNOTATION_DESCRIPTION,
        QByteArray(text.encode("utf-8")),
    )


def document_key(doc):
    if doc is None:
        return None
    try:
        name = doc.name()
        path = doc.fileName()
    except Exception:
        return None
    try:
        root_uuid = str(doc.rootNode().uniqueId())
    except Exception:
        root_uuid = ""
    return (name, path, root_uuid)


_document_managers = weakref.WeakValueDictionary()


def register_document_manager(doc, instance):
    """Record the docker-owned manager for a document."""
    key = document_key(doc)
    if key is not None:
        _document_managers[key] = instance


def unregister_document_manager(key, instance):
    if key is not None and _document_managers.get(key) is instance:
        _document_managers.pop(key, None)


def manager_for_document(doc):
    """Return the docker manager for *doc*, else the shared fallback."""
    key = document_key(doc)
    if key is not None:
        found = _document_managers.get(key)
        if found is not None:
            return found
    return manager


manager = SessionManager()

# Window Capture Layer

Capture application windows into per-document Krita paint layers from the **Window Capture** docker.

Supports Krita 5.x (PyQt5) and 6.x (PyQt6). Linux requires X11 and `xdotool`; Windows requires no extra tools.

## Install

1. **Tools > Scripts > Import Python Plugin from File...** and select `window-capture-layer.zip`.
2. Restart Krita.
3. **Settings > Configure Krita > Python Plugin Manager** — enable **Window Capture Layer**.
4. Restart Krita.

## Docker

Sessions are scoped to the active document, saved with it, and restored as `paused` (`✗`) on reopen.

- **@:** Shows instructions, credits, capture backend status, dependencies, recent warnings, and (on Windows) log-reporting steps.
- **Window & choose:** Combo re-scans visible windows (largest first) on open; **choose** blocks the docker for up to 30 s until you click a window.
- **add:** Creates a `Window capture XXXX` layer with a child transform mask. Live sessions update when Krita regains focus (no background timer).
- **refresh / refresh all:** Captures selected or all document sessions now (paused sessions stay paused).
- **toggle / toggle all:** Pauses or resumes the selected session, or pauses all if any are running / starts stopped sessions otherwise.
- **apply:** Removes the session from the docker and document save data while keeping the layer in Krita.

**States:** `active` (`✓`), `paused` (`✗`), `lost` (`!`, window missing; retried on focus-in or start), `error` (`⚠`, invalid document/layer or write failure).

### Transform Workflow

Select the capture layer's **Transform Mask** and use Krita's Transform tool there. Captures overwrite raw layer pixels directly and never touch masks.

## Actions (`Tools > Scripts`)

| Action | Behavior |
| --- | --- |
| **Capture Window to New Layer** | Prompts for a title substring and captures the largest matching window once. |
| **Toggle Live Window Capture** | Pauses active sessions, or prompts to add and start a new one. |
| **Insert Channel Test Pattern** | Writes red, green, blue, and white RGBA bands to verify channel order. |

## Limits & Caveats

- **Limits:** Up to 8 sessions; windows larger than 4K UHD (`3840x2160`) are skipped.
- **Document format:** `RGBA` / `U8` only (no color-space conversion).
- **Undo & painting:** Captures mark the document modified, are not undoable, and can overwrite live capture layers mid-stroke.
- **Linux (X11):** Wayland is unsupported. Uses XComposite window redirection (works without a compositor and captures occluded windows), falling back to Qt window grab when unavailable.
- **Windows:** Captures client area without title bars. Some hardware-accelerated windows render black natively and fall back to Qt (which captures visible on-screen pixels, including overlaps).

## Troubleshooting

- **Native capture unavailable / black frame:** Uses Qt fallback automatically. Force comparison with `WINDOW_CAPTURE_LAYER_NATIVE=0` before launching Krita.
- **`xdotool not found` (Linux):** Install `xdotool` (`apt install xdotool`, `pacman -S xdotool`, or `dnf install xdotool`) and restart Krita.
- **Debug logs:** Set `WINDOW_CAPTURE_LAYER_DEBUG=1` (`INFO`) or `2` (`DEBUG`), and optionally `WINDOW_CAPTURE_LAYER_LOG=<path>`, before starting Krita.

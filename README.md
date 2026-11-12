# Window Capture Layer

Krita docker that captures application windows into paint layers. Several
capture sessions can run at once, each writing to its own layer. A docker lists the
sessions of the active document, so switching document tabs switches the list.
A second Krita main window is intended to have its own independent docker and
session list; this is untested.
Deleting a capture layer deletes its session instance. Each capture layer gets
a transform mask; use it to move or scale the capture.

Version 0.8.0 (stable). Requires Krita 5.x (PyQt5; PyQt6 fallback, untested).
On Linux it needs an X11 session and `xdotool`. On Windows no extra tools are
needed.

## Install

1. In Krita: Tools > Scripts > Import Python Plugin from File...
2. Select `window-capture-layer.zip`.
3. Restart Krita.
4. Enable **Window Capture Layer** in
   Settings > Configure Krita > Python Plugin Manager.
5. Restart Krita again.

## Docker

**Window Capture** docker (right dock):

```text
@
Window
[window combo] [add] [choose]

[session table]

[refresh] [refresh all] [toggle] [toggle all] [apply]
[status]
```

- **@** (top left) shows quick instructions, credits, the capture method in use,
  dependency status, and any recent capture warnings. On Windows it also shows a
  short guide for reporting capture problems.
- The **Window** combo re-scans visible windows (largest first) every time its
  popup opens. **choose** waits up to 30 s for you to click the wanted window;
  the docker is blocked until you click or the wait ends.
- **add** creates a layer named `Window capture XXXX`. Live sessions capture one
  frame when Krita becomes the active application; there is no periodic timer.
  Switch away and back to refresh the captured window.
- Table columns: layer, window title, state. Long titles are shortened; hover
  to see the full title. State uses a mark: `✓` running, `✗` paused, `!` lost,
  `⚠` error. Hover the mark for the full state and any error text.
- **refresh** captures the selected session now, including a paused one; it
  stays paused. **refresh all** captures every session of the active document,
  including paused ones; paused ones stay paused.
- **toggle** starts or pauses the selected session. **toggle all** pauses every
  session when anything is running, otherwise starts every stopped session.
  **apply** detaches the selected session: it disappears from the table and is
  no longer saved with the document, but its capture layer stays in the Layers
  panel, disconnected from the plugin. The table stretches with the docker
  width.

States: `active`, `paused`, `lost` (window gone; retried on the next Krita
focus-in or explicit start), `error` (unsupported document, missing layer, or a
failed write).

Sessions are stored per document and restored as `paused` when the document is
reopened. The table shows only sessions for the active document. Deleting a
session's layer removes that session instance and its saved entry. **apply**
drops only the row and saved entry, keeping the layer for normal painting.
Renaming the layer keeps it linked. A
restored session whose layer is missing shows `layer missing`; **toggle** retries
the lookup. Tools > Scripts actions use the docker manager for the active
document.

### Transform workflow

New capture layers already have a transform mask. Select it in the Layers panel
and use Krita's Transform tool on the mask. The plugin only writes raw pixels to
the capture layer, never to masks, so mask transformations survive updates. Use
the mask rather than the Move tool on the layer itself.

## Actions (Tools > Scripts)

| Action | What it does |
| --- | --- |
| Capture Window to New Layer | Prompt for a title substring, capture the largest match once. |
| Toggle Live Window Capture | Pause all active sessions, or add a new one. |
| Insert Channel Test Pattern | Red / green / blue / white bands from known RGBA bytes. |

## Limits

At most 8 sessions, 4K UHD maximum window size (larger windows are skipped,
never truncated). Live sessions capture on Krita focus-in, not on a timer; lost
windows are retried on the next focus-in or manual start.

## Known limits

- Linux: X11 only. Wayland is not supported; window capture returns a null
  image there. The plugin redirects the window itself, so occluded windows are
  captured even without a running compositor; Qt's capture is the fallback when
  the XComposite path is unavailable or the image layout is unsupported.
- Windows: captures the client area without the title bar. Covered windows keep
  updating for most applications, but some hardware-accelerated apps can render
  black; those need a different capture method and are a known gap.
- When the native capture path is unavailable, or a window renders black, the
  plugin falls back to Qt's capture and the **@** dialog lists the reason. The
  Qt fallback only sees on-screen pixels, so a covered window can capture
  whatever overlaps it.
- RGBA / U8 documents only; no color space conversion. Other documents are
  rejected with a session error.
- Captures are not undoable. The document is marked modified so the save prompt
  appears.
- One refresh per focus-in per touched document; many large sessions capture as
  a single burst when Krita regains focus.
- Avoid painting on a live capture layer: a capture may overwrite pixels during
  an open stroke.
- Window frames are written at full size, cropped only by the canvas bounds.

## Troubleshooting

- The **@** dialog says the native capture path is unavailable: the plugin is
  already using Qt's fallback. To force that comparison, set
  `WINDOW_CAPTURE_LAYER_NATIVE=0` before starting Krita.
- The **@** dialog says `xdotool not found`: install it and restart Krita.
  - Debian/Ubuntu: `sudo apt install xdotool`
  - Arch: `sudo pacman -S xdotool`
  - Fedora: `sudo dnf install xdotool`
  Without `xdotool`, the window list and **choose** button cannot work.
- To collect a log for a bug report, set `WINDOW_CAPTURE_LAYER_DEBUG=1` (INFO)
  or `WINDOW_CAPTURE_LAYER_DEBUG=2` (verbose), and
  `WINDOW_CAPTURE_LAYER_LOG=<path>` to write to a file instead of the terminal.
  On Windows set them in the Command Prompt before starting Krita.

"""Phase 3, Step 2 — capture plumbing (the de-risking spike).

Prove the **screenshot → vision** pipeline in isolation, before any
learner-facing verification rests on it. This module:

- grabs ONE frame on demand via macOS `screencapture` (one explicit call, never
  continuous or periodic);
- defaults to the **active editor window**, not the whole desktop, choosing it
  non-interactively by window id (whole-screen is an opt-in fallback);
- handles the alt-tab edge case — if the frontmost window is the tutor's own
  terminal, it skips it and grabs the last non-tutor window instead (it never
  captures the tutor looking at itself);
- fails the macOS Screen-Recording permission gate with a clear, human message
  telling you what to enable — not a stack trace;
- writes the frame to a temp file and cleans it up after the round-trip.

Nothing here touches `explore`, `tutor.py`, or the judge schema. Step 3
(comprehension verification) reuses this plumbing, feeding a captured frame to
the existing `Verdict` judge with image evidence.

Run the standalone de-risking test (capture one frame, ask Opus 4.8 to read the
code/text back so you can confirm legibility before building on it):

    python -m repo_whisperer.capture
    python -m repo_whisperer.capture --screen          # whole-screen fallback
    python -m repo_whisperer.capture --window 185       # a specific window id
    python -m repo_whisperer.capture --list             # list capturable windows
    python -m repo_whisperer.capture --no-vision --keep  # just save the PNG
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from repo_whisperer.answer import ANSWER_MODEL, _client

# The macOS screenshot tool. Absolute path so a tampered PATH can't shadow it.
SCREENCAPTURE = "/usr/sbin/screencapture"

# Owner names we never want as a capture target.
#
# TERMINAL_OWNERS: dedicated terminal emulators. The tutor usually runs in one,
# so when you alt-tab to it to trigger a capture it becomes frontmost — we skip
# it and grab the window behind it (the editor). Deliberately does NOT include
# editor apps (VS Code, Cursor, Windsurf): those commonly host an integrated
# terminal AND show the code we want, so they stay capturable. Override per-run
# with --exclude, or pin an exact window with --window.
TERMINAL_OWNERS = frozenset(
    {
        "Terminal",
        "iTerm2",
        "iTerm",
        "WezTerm",
        "Alacritty",
        "kitty",
        "Ghostty",
        "Hyper",
        "Tabby",
        "rio",
        "Warp",
    }
)

# System / overlay surfaces that are never a meaningful capture target.
SYSTEM_OWNERS = frozenset(
    {
        "Window Server",
        "WindowServer",
        "Dock",
        "Control Center",
        "Notification Center",
        "Spotlight",
        "SystemUIServer",
        "Wallpaper",
        "Screenshot",
        "screencapture",
    }
)

# Ignore tiny windows (menu-bar items, HUDs, pickers) when choosing a target.
MIN_WINDOW_W = 200
MIN_WINDOW_H = 200

# Reuse the Phase 1 answering model for the vision round-trip (one source).
VISION_MODEL = ANSWER_MODEL
MAX_VISION_TOKENS = 1500

# A capture smaller than this almost certainly means a failed/blocked grab
# (e.g. permission silently denied), not a real screenshot.
_MIN_VALID_BYTES = 1024

PERMISSION_MESSAGE = (
    "macOS Screen Recording permission is not granted, so the screen can't be "
    "captured.\n\n"
    "Enable it for the app running this tutor (your terminal or editor — e.g. "
    "Terminal, iTerm, or Cursor/VS Code):\n"
    "  System Settings → Privacy & Security → Screen Recording → turn it on for "
    "that app,\n"
    "then fully quit and reopen the app so the new permission takes effect.\n\n"
    "(A system prompt may have just appeared asking for this — granting it there "
    "still requires reopening the app.)"
)

# What the vision call is asked to do. A faithful-transcription ask so the
# de-risking test measures legibility, not the model's cleverness.
VISION_PROMPT = (
    "This is a screenshot of a developer's screen, most likely a code editor. "
    "Read it carefully and transcribe the code or text you can see, as exactly "
    "as possible — preserve indentation and structure. If you can tell the file "
    "name or programming language, say so first. If part of the image is too "
    "blurry or small to read, say which part rather than guessing. If there is "
    "no readable code or text at all, say that plainly."
)


class CaptureError(RuntimeError):
    """A screenshot could not be taken (and it isn't a permission problem)."""


class PermissionDeniedError(CaptureError):
    """Screen Recording permission is not granted. Carries the human message."""


@dataclass
class Frame:
    """One captured screenshot frame on disk.

    `path` is a temp PNG; the standalone runner (and Step 3) delete it after
    use via `captured_frame()`. For a window capture, `owner`/`window_id`/size
    describe what was grabbed so the caller can confirm it hit the right window
    (`title` is best-effort and is often empty until Screen Recording permission
    is granted).
    """

    path: str
    mode: str  # "window" | "screen"
    owner: str = ""
    window_id: int | None = None
    width: int = 0
    height: int = 0
    title: str = ""


# --- macOS Screen-Recording permission ------------------------------------


def has_screen_permission() -> bool:
    """Whether this process may capture the screen (best-effort preflight).

    Uses CoreGraphics' preflight check. If the symbol is unavailable for any
    reason, returns True so we don't wrongly block — a genuinely blocked grab is
    still caught downstream by the empty-capture check.
    """
    try:
        import Quartz

        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


def request_screen_permission() -> bool:
    """Trigger the one-time system permission prompt, then re-check.

    macOS shows its Screen Recording prompt only once per app; after that the
    user must toggle it in System Settings. Returns the post-request preflight.
    """
    try:
        import Quartz

        Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        pass
    return has_screen_permission()


def _ensure_permission() -> None:
    """Raise PermissionDeniedError with a human message if we can't capture."""
    if has_screen_permission():
        return
    if request_screen_permission():
        return
    raise PermissionDeniedError(PERMISSION_MESSAGE)


# --- window enumeration & target selection --------------------------------


def _onscreen_windows() -> list[dict]:
    """On-screen normal windows, front-to-back, big enough to be real targets.

    Filters to layer-0 application windows (drops the menu bar, Dock, Control
    Center, notifications, and other overlays) above the minimum size. Owner,
    id, and bounds are available without Screen Recording permission; window
    titles are redacted to "" without it, which is fine — we select by id.
    """
    import Quartz

    options = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements
    )
    raw = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []

    windows: list[dict] = []
    for w in raw:
        if w.get("kCGWindowLayer") != 0:
            continue
        owner = (w.get("kCGWindowOwnerName") or "").strip()
        if not owner or owner in SYSTEM_OWNERS:
            continue
        bounds = w.get("kCGWindowBounds") or {}
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
        if width < MIN_WINDOW_W or height < MIN_WINDOW_H:
            continue
        wid = w.get("kCGWindowNumber")
        if wid is None:
            continue
        windows.append(
            {
                "id": int(wid),
                "owner": owner,
                "title": (w.get("kCGWindowName") or "").strip(),
                "width": width,
                "height": height,
            }
        )
    return windows


def list_windows() -> list[dict]:
    """Public view of capturable windows (front-to-back), for `--list`."""
    return _onscreen_windows()


def pick_target_window(exclude: frozenset[str] | set[str] | tuple = ()) -> dict | None:
    """Front-most window that is not the tutor's terminal (or `exclude`d).

    This is the "last non-tutor window" rule: walk front-to-back and return the
    first application window whose owner isn't a terminal emulator (or an extra
    excluded name). Returns None if nothing qualifies (e.g. only the tutor and
    system surfaces are on screen) — the caller then suggests `--screen`.
    """
    excludes = TERMINAL_OWNERS | set(exclude)
    for w in _onscreen_windows():
        if w["owner"] in excludes:
            continue
        return w
    return None


# --- capture ---------------------------------------------------------------


def _run_screencapture(extra_args: list[str], dest: str) -> None:
    """Invoke `screencapture` and validate that a real image landed on disk.

    Distinguishes a permission failure (re-raised as PermissionDeniedError with
    the human message) from any other capture failure (CaptureError).
    """
    cmd = [SCREENCAPTURE, "-x", "-t", "png", *extra_args, dest]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError as exc:
        raise CaptureError(
            f"Could not run screencapture at {SCREENCAPTURE}."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("screencapture timed out.") from exc

    ok = (
        proc.returncode == 0
        and os.path.exists(dest)
        and os.path.getsize(dest) >= _MIN_VALID_BYTES
    )
    if ok:
        return

    # Failed grab. The usual silent cause is a revoked/absent permission.
    if not has_screen_permission():
        raise PermissionDeniedError(PERMISSION_MESSAGE)
    detail = (proc.stderr or proc.stdout or "").strip()
    raise CaptureError(
        "screencapture did not produce a usable image"
        + (f": {detail}" if detail else ".")
    )


def capture(
    mode: str = "window",
    exclude: frozenset[str] | set[str] | tuple = (),
    window_id: int | None = None,
    title: str = "",
) -> Frame:
    """Take one screenshot and return the `Frame` describing it.

    - mode="window" (default): capture the chosen window. If `window_id` is
      given, capture exactly that window; otherwise pick the front-most
      non-tutor window. Raises CaptureError (suggesting `--screen`) if no window
      qualifies.
    - mode="screen": capture the whole main display.

    Raises PermissionDeniedError (carrying `PERMISSION_MESSAGE`) if Screen
    Recording isn't granted, and ValueError via `_client` only later, at the
    vision step — not here.
    """
    _ensure_permission()

    fd, dest = tempfile.mkstemp(prefix="repo_whisperer_cap_", suffix=".png")
    os.close(fd)

    try:
        if mode == "screen":
            _run_screencapture([], dest)
            return Frame(path=dest, mode="screen")

        owner = ""
        width = height = 0
        if window_id is None:
            target = pick_target_window(exclude)
            if target is None:
                raise CaptureError(
                    "No editor window found to capture (only the tutor's "
                    "terminal and system windows are on screen). Bring your "
                    "editor forward, or use --screen / --window <id>."
                )
            window_id = target["id"]
            owner = target["owner"]
            title = target["title"]
            width, height = target["width"], target["height"]

        # `-l<id>` grabs that window non-interactively; `-o` drops the shadow.
        _run_screencapture([f"-l{window_id}", "-o"], dest)
        return Frame(
            path=dest,
            mode="window",
            owner=owner,
            window_id=window_id,
            width=width,
            height=height,
            title=title,
        )
    except BaseException:
        # Never leave a temp artifact behind on a failed capture.
        if os.path.exists(dest):
            os.unlink(dest)
        raise


@contextmanager
def captured_frame(
    mode: str = "window",
    exclude: frozenset[str] | set[str] | tuple = (),
    window_id: int | None = None,
    keep: bool = False,
) -> Iterator[Frame]:
    """Capture a frame and guarantee its temp file is cleaned up afterward.

    Yields the `Frame`; deletes its file on exit unless `keep=True`. This is how
    callers (the standalone runner now, Step 3 later) avoid leaving screenshots
    of the user's screen lying around on disk.
    """
    frame = capture(mode=mode, exclude=exclude, window_id=window_id)
    try:
        yield frame
    finally:
        if not keep and os.path.exists(frame.path):
            os.unlink(frame.path)


# --- the vision round-trip (the actual de-risking test) -------------------


def image_block(frame: Frame) -> dict:
    """The Anthropic image content block (base64 PNG) for `frame`.

    Factored out of `read_frame` so other modules can compose their OWN vision
    message around a captured frame without duplicating the encoding — Step 3's
    screen-aware tutor builds an image-block + teaching-instruction message this
    way. The frame's PNG is read fresh from disk on each call, so the caller must
    keep the file alive (e.g. inside `captured_frame`) until the request is sent.
    """
    with open(frame.path, "rb") as fh:
        encoded = base64.standard_b64encode(fh.read()).decode("ascii")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": encoded,
        },
    }


def read_frame(frame: Frame, model: str = VISION_MODEL) -> str:
    """Send the captured frame to the model and return what it reads back.

    This is the point of Step 2: confirm Opus can read code legibly off a real
    screenshot before verification is built on top. Raises ValueError (via
    `_client`) if the API key is missing.
    """
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_VISION_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block(frame),
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }
        ],
    )
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


# --- standalone runner -----------------------------------------------------


def describe_frame(frame: Frame) -> str:
    """One-line summary of what was captured, for the terminal."""
    if frame.mode == "screen":
        return "whole screen"
    label = frame.owner or "window"
    if frame.title:
        label += f" — {frame.title!r}"
    return f"{label} (id={frame.window_id}, {frame.width}x{frame.height})"


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m repo_whisperer.capture",
        description=(
            "Capture one screenshot of your active editor window and ask Opus "
            "4.8 to read the code back (Phase 3 Step 2 de-risking test)."
        ),
    )
    parser.add_argument(
        "--screen", action="store_true",
        help="capture the whole screen instead of the active window",
    )
    parser.add_argument(
        "--window", type=int, metavar="ID", dest="window_id",
        help="capture an exact window id (see --list)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="APP",
        help="extra app/owner name to skip when picking a window (repeatable)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list capturable windows (with ids) and exit, without capturing",
    )
    parser.add_argument(
        "--no-vision", action="store_true", dest="no_vision",
        help="just capture and save the PNG; skip the vision call",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="keep the temp PNG instead of deleting it after the round-trip",
    )
    parser.add_argument(
        "--model", default=VISION_MODEL,
        help=f"vision model id (default: {VISION_MODEL})",
    )
    args = parser.parse_args(argv[1:])

    if args.list:
        try:
            windows = list_windows()
        except Exception as exc:  # Quartz import/availability issues
            print(f"error: could not list windows: {exc}", file=sys.stderr)
            return 1
        if not windows:
            print("No capturable windows found.")
            return 0
        print("Capturable windows (front-to-back):")
        excludes = TERMINAL_OWNERS | set(args.exclude)
        for w in windows:
            skip = " [skipped: terminal/tutor]" if w["owner"] in excludes else ""
            title = f"  {w['title']!r}" if w["title"] else ""
            print(
                f"  id={w['id']:<7} {w['owner']:<22} "
                f"{w['width']}x{w['height']}{title}{skip}"
            )
        return 0

    mode = "screen" if args.screen else "window"
    # --no-vision is only useful if the file survives, so it implies --keep.
    keep = args.keep or args.no_vision

    try:
        with captured_frame(
            mode=mode,
            exclude=set(args.exclude),
            window_id=args.window_id,
            keep=keep,
        ) as frame:
            print(f"Captured {describe_frame(frame)}")
            if keep:
                print(f"Saved: {frame.path}")
            if args.no_vision:
                return 0
            print("Asking the model to read it back...\n")
            try:
                reading = read_frame(frame, model=args.model)
            except ValueError as exc:  # missing API key
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print("-" * 60)
            print(reading)
            print("-" * 60)
        return 0
    except PermissionDeniedError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))

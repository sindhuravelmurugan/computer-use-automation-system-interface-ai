"""``WebSurface``: the Playwright (sync, accessibility-tree-driven)
implementation of the ``Surface`` protocol.

This is the *only* module in the codebase allowed to import ``playwright``.
The agent loop and the replay engine see ``src.surface.protocol.Surface``
and never this class directly (they take a ``Surface`` and don't care which
implementation it is).

Note on perception: Playwright's legacy ``page.accessibility`` snapshot API
has been removed from the installed Playwright version, and ``aria_snapshot``
gives a formatted string rather than per-node bounds, refs, and adjacency —
none of which we can afford to lose (docs/surface-spec.md's ``nearby_text``
requirement in particular). So perception here is a small, in-page
accessible-name/role computation over the raw DOM, run per frame and
flattened in Python. It approximates the real accessibility tree closely
enough for this app's markup (real ``<label for>`` bindings, semantic
buttons/links/tables) without depending on a browser API this Playwright
version no longer exposes.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Error as PlaywrightError,
)
from playwright.sync_api import (
    Frame,
    Locator,
    Page,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.sync_api import (
    sync_playwright,
)

from src.schema.common import DomStrategy
from src.surface.matching import resolve_bundle
from src.surface.pruning import prune_nodes
from src.surface.types import (
    Action,
    ActionResult,
    ControllerViolation,
    ErrorCode,
    LocatorBundle,
    Observation,
    PageSignature,
    Rect,
    Resolution,
    SessionHandle,
    UINode,
    WaitSpec,
)

# --- In-page perception -------------------------------------------------- #
#
# Shared helper functions, spliced into two separate evaluate() calls (bulk
# collection and single-element description) since each is its own JS
# function scope. Kept as one Python string so there is exactly one place
# that defines "what a role/name/value is" for this surface.

_JS_HELPERS = r"""
  function isVisible(el) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (el.hidden) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function computeRole(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.toLowerCase();
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'hidden') return null;
      if (['submit', 'button', 'reset'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    return null;
  }

  function computeName(el, tag) {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const txt = labelledby.split(/\s+/).map((id) => {
        const t = document.getElementById(id);
        return t ? t.textContent.trim() : '';
      }).filter(Boolean).join(' ');
      if (txt) return txt;
    }

    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      if (el.id) {
        const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (label && label.textContent.trim()) return label.textContent.trim();
      }
      const wrapping = el.closest('label');
      if (wrapping && wrapping.textContent.trim()) return wrapping.textContent.trim();
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (['submit', 'button', 'reset'].includes(type) && el.value) return el.value.trim();
      if (el.placeholder) return el.placeholder.trim();
      return '';
    }

    const title = el.getAttribute('title');
    const text = el.textContent.trim();
    if (text) return text;
    if (title) return title.trim();
    return '';
  }

  function computeValue(el, tag) {
    if (tag === 'input' || tag === 'textarea') return el.value;
    if (tag === 'select') {
      const opt = el.options[el.selectedIndex];
      return opt ? opt.textContent.trim() : null;
    }
    return null;
  }

  function computeEnabled(el) {
    if ('disabled' in el && el.disabled) return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    return true;
  }

  function nearbyText(el) {
    const cell = el.closest('td,th');
    if (cell) {
      const row = cell.closest('tr');
      if (row) {
        return Array.from(row.children)
          .filter((td) => td !== cell)
          .map((td) => td.textContent.trim())
          .filter(Boolean);
      }
    }
    const parent = el.parentElement;
    if (!parent) return [];
    return Array.from(parent.children)
      .filter((sib) => sib !== el)
      .map((sib) => sib.textContent.trim())
      .filter(Boolean)
      .slice(0, 3);
  }

  function domHint(el) {
    if (el.id) return '#' + el.id;
    const cls = (el.className && typeof el.className === 'string')
      ? el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.')
      : '';
    return cls ? el.tagName.toLowerCase() + '.' + cls : el.tagName.toLowerCase();
  }
"""

_COLLECT_JS = (
    "(startIndex) => {\n"
    + _JS_HELPERS
    + r"""
  document.querySelectorAll('[data-surface-ref]').forEach((el) => {
    el.removeAttribute('data-surface-ref');
  });

  const results = [];
  let ref = startIndex;
  const consumed = new Set();

  const structural = Array.from(document.querySelectorAll(
    'button, a[href], select, textarea, input, h1, h2, h3, h4, h5, h6, ' +
    '[role="button"], [role="link"], [role="checkbox"], [role="radio"], ' +
    '[role="combobox"], [role="menuitem"], [role="heading"], ' +
    '[role="alert"], [role="alertdialog"], [role="dialog"], [role="status"]'
  ));

  for (const el of structural) {
    const tag = el.tagName.toLowerCase();
    const role = computeRole(el);
    if (!role) continue;
    const rect = el.getBoundingClientRect();
    const nodeRef = 'n' + (ref++);
    el.setAttribute('data-surface-ref', nodeRef);
    results.push({
      ref: nodeRef,
      role: role,
      name: computeName(el, tag),
      value: computeValue(el, tag),
      enabled: computeEnabled(el),
      visible: isVisible(el),
      bounds: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      nearby_text: nearbyText(el),
      dom_hint: domHint(el),
    });
    consumed.add(el);
  }

  const all = document.querySelectorAll('body *');
  for (const el of all) {
    if (consumed.has(el)) continue;
    if (el.children.length > 0) continue;
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'option', 'iframe', 'head', 'title'].includes(tag)) continue;
    const text = el.textContent.trim();
    if (!text) continue;
    const rect = el.getBoundingClientRect();
    const nodeRef = 'n' + (ref++);
    el.setAttribute('data-surface-ref', nodeRef);
    results.push({
      ref: nodeRef,
      role: 'text',
      name: text,
      value: null,
      enabled: true,
      visible: isVisible(el),
      bounds: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      nearby_text: nearbyText(el),
      dom_hint: domHint(el),
    });
  }

  return {nodes: results, next_ref: ref};
}
"""
)

_DESCRIBE_JS = (
    "(el) => {\n"
    + _JS_HELPERS
    + r"""
  const tag = el.tagName.toLowerCase();
  const role = computeRole(el) || 'text';
  const rect = el.getBoundingClientRect();
  return {
    role: role,
    name: computeName(el, tag) || el.textContent.trim(),
    value: computeValue(el, tag),
    enabled: computeEnabled(el),
    visible: isVisible(el),
    bounds: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    nearby_text: nearbyText(el),
    dom_hint: domHint(el),
  };
}
"""
)

_TAG_JS = "(el, ref) => { el.setAttribute('data-surface-ref', ref); }"

# --- human action capture (docs/escalation-spec.md §4) -------------------- #
#
# Installed once (via Playwright's context-level expose_binding /
# add_init_script -- the same CDP primitives, Runtime.addBinding and
# Page.addScriptToEvaluateOnNewDocument, a hand-rolled CDP session would
# call) and then gated on/off by `_capture_active` across release()/
# reacquire() pairs, rather than added and torn down each time: Playwright
# has no clean "remove a binding" call, and re-adding an init script on every
# release would leak listeners across navigations. What matters per the spec
# is that nothing is recorded outside a human's turn, which the gate gives
# for free without needing the removal itself to be real.
# Written as a self-invoking expression -- not an arrow function Playwright
# calls with an argument -- so the exact same string works both as
# `frame.evaluate(...)` (installs on the currently-loaded document) and as
# `context.add_init_script(...)` (installs on every future document), no
# separate wrapping needed for either call site.
_HUMAN_CAPTURE_JS = (
    "(() => {\n"
    + _JS_HELPERS
    + r"""
  if (window.__humanCaptureInstalled) return;
  window.__humanCaptureInstalled = true;
  const report = (kind, el, valueRedacted) => {
    if (!(el instanceof Element)) return;
    const tag = el.tagName.toLowerCase();
    const role = computeRole(el) || tag;
    const name = (computeName(el, tag) || el.textContent.trim()).slice(0, 120);
    window.__humanAction(JSON.stringify({kind, role, name, value_redacted: !!valueRedacted}));
  };
  document.addEventListener('click', (e) => report('click', e.target, false), true);
  document.addEventListener('input', (e) => report('input', e.target, true), true);
  document.addEventListener('change', (e) => report('change', e.target, true), true);
  document.addEventListener('submit', (e) => report('submit', e.target, false), true);
})()
"""
)

_APP_VERSION_RE = re.compile(r"v\d+\.\d+\.\d+")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _compute_state_hash(nodes: list[UINode]) -> str:
    canonical = "\n".join(f"{n.role}|{n.name}|{'/'.join(n.frame_path)}" for n in nodes)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WebSurface:
    """Playwright sync implementation of ``Surface`` for a browser-rendered
    web app, including legacy framesets.
    """

    surface_type = "web"

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page: Page | None = None
        self._run_id: str | None = None
        self._debug_port: int | None = None
        # Control-transfer state (docs/escalation-spec.md §1, §3-4).
        self._controller_released = False
        self._trace_segment = 0
        self._capture_installed = False
        self._capture_active = False
        self._human_action_seq = 0

    # --- lifecycle --------------------------------------------------- #

    def open(self, entry_point: str, run_id: str) -> None:
        self._run_id = run_id
        self._playwright = sync_playwright().start()
        self._debug_port = _find_free_port()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=[f"--remote-debugging-port={self._debug_port}"],
        )
        self._context = self._browser.new_context()
        self._context.tracing.start(screenshots=True, snapshots=True)
        self._page = self._context.new_page()
        self._page.goto(entry_point)
        self._perform_wait(WaitSpec(strategy="settle", timeout_ms=10_000))

    def close(self, keep_trace: bool) -> None:
        assert self._context is not None
        if keep_trace:
            assert self._run_id is not None
            trace_dir = Path("evidence") / self._run_id
            trace_dir.mkdir(parents=True, exist_ok=True)
            self._context.tracing.stop(path=str(trace_dir / "trace.zip"))
        else:
            self._context.tracing.stop()
        self._context.close()
        assert self._browser is not None
        self._browser.close()
        assert self._playwright is not None
        self._playwright.stop()

    # --- perception ---------------------------------------------------- #

    def observe(self) -> Observation:
        assert self._page is not None
        raw_nodes = self._collect_raw_nodes()
        pruned = prune_nodes(raw_nodes)

        heading = self._page.evaluate(
            "() => { const h = document.querySelector('h1, h2'); "
            "return h ? h.textContent.trim() : null; }"
        )
        body_text = self._page.inner_text("body")
        version_match = _APP_VERSION_RE.search(body_text)

        page_signature = PageSignature(
            url=self._page.url,
            title=self._page.title(),
            heading=heading,
            app_version=version_match.group(0) if version_match else None,
        )

        return Observation(
            nodes=pruned,
            page=page_signature,
            # Screenshots are event-driven evidence (docs/surface-spec.md
            # §5), captured explicitly via capture_screenshot() — never as
            # a side effect of a read here, so observe() stays pure.
            screenshot=None,
            state_hash=_compute_state_hash(pruned),
            observed_at=datetime.now(timezone.utc),
        )

    def _collect_raw_nodes(self) -> list[UINode]:
        assert self._page is not None
        nodes: list[UINode] = []
        counter = 0
        for frame in self._page.frames:
            try:
                result: dict[str, Any] = frame.evaluate(_COLLECT_JS, counter)
            except PlaywrightError:
                # A frame that hasn't finished attaching/navigating yet.
                continue
            counter = result["next_ref"]
            path = self._frame_path_for(frame)
            for raw in result["nodes"]:
                nodes.append(
                    UINode(
                        ref=raw["ref"],
                        role=raw["role"],
                        name=raw["name"],
                        value=raw["value"],
                        enabled=raw["enabled"],
                        visible=raw["visible"],
                        frame_path=path,
                        bounds=Rect(**raw["bounds"]),
                        nearby_text=raw["nearby_text"],
                        dom_hint=raw["dom_hint"],
                    )
                )
        return nodes

    def _frame_path_for(self, frame: Frame) -> list[str]:
        chain: list[Frame] = []
        f = frame
        while f.parent_frame is not None:
            chain.append(f)
            f = f.parent_frame
        path = ["main"]
        path.extend(self._frame_label(child) for child in reversed(chain))
        return path

    @staticmethod
    def _frame_label(frame: Frame) -> str:
        if frame.name:
            return frame.name
        tail = frame.url.rstrip("/").rsplit("/", 1)[-1]
        return tail or f"frame{id(frame)}"

    def _frame_by_path(self, path: list[str]) -> Frame:
        # Deliberately not a child_frames walk from main_frame: that API has
        # been observed to hand back a stale, already-detached Frame object
        # for an iframe that has since navigated (its initial about:blank
        # placeholder lingers in the parent's child list). page.frames is
        # the flat list Playwright itself keeps current, so matching against
        # that avoids ever handing act() a dead frame.
        assert self._page is not None
        for frame in self._page.frames:
            if not frame.is_detached() and self._frame_path_for(frame) == path:
                return frame
        raise RuntimeError(f"no attached frame matches path {path!r}")

    # --- targeting ------------------------------------------------------ #

    def resolve(self, bundle: LocatorBundle) -> Resolution:
        nodes = prune_nodes(self._collect_raw_nodes())
        return resolve_bundle(bundle, nodes, dom_matcher=self._match_dom)

    def _match_dom(self, strategy: DomStrategy) -> list[UINode]:
        assert self._page is not None
        selector = strategy.css if strategy.css is not None else f"xpath={strategy.xpath}"
        matches: list[UINode] = []
        counter = 0
        for frame in self._page.frames:
            try:
                locator = frame.locator(selector)
                count = locator.count()
            except PlaywrightError:
                continue
            path = self._frame_path_for(frame)
            for i in range(count):
                ref = f"dom{counter}"
                counter += 1
                node = self._describe(locator.nth(i), path, ref)
                if node is not None:
                    matches.append(node)
        return matches

    def _describe(self, locator: Locator, frame_path: list[str], ref: str) -> UINode | None:
        try:
            locator.evaluate(_TAG_JS, ref)
            info = locator.evaluate(_DESCRIBE_JS)
        except PlaywrightError:
            return None
        return UINode(
            ref=ref,
            role=info["role"],
            name=info["name"],
            value=info["value"],
            enabled=info["enabled"],
            visible=info["visible"],
            frame_path=frame_path,
            bounds=Rect(**info["bounds"]),
            nearby_text=info["nearby_text"],
            dom_hint=info["dom_hint"],
        )

    # --- action ----------------------------------------------------------- #

    def act(self, action: Action) -> ActionResult:
        assert self._page is not None
        if self._controller_released:
            raise ControllerViolation(
                "automation attempted to act while control of the session is "
                "ceded to a human (docs/escalation-spec.md §1)"
            )
        start = time.monotonic()

        if action.kind == "navigate":
            assert action.url is not None
            self._page.goto(action.url)
            return self._finish_act(start, ok=True, resolution=None, error_code=None, wait=action.wait)

        if action.kind == "wait_for":
            try:
                self._perform_wait(action.wait)
            except PlaywrightTimeoutError:
                return self._finish_act(
                    start, ok=False, resolution=None, error_code="TIMEOUT", wait=None
                )
            return self._finish_act(start, ok=True, resolution=None, error_code=None, wait=None)

        resolution: Resolution | None = None
        if action.target is not None:
            resolution = self.resolve(action.target)
            if resolution.status != "resolved":
                error_code: ErrorCode = "AMBIGUOUS" if resolution.status == "ambiguous" else "NOT_FOUND"
                return self._finish_act(
                    start, ok=False, resolution=resolution, error_code=error_code, wait=None
                )

        node = resolution.node if resolution is not None else None

        try:
            if action.kind in ("click", "type", "select") and node is not None:
                frame = self._frame_by_path(node.frame_path)
                locator = frame.locator(f'[data-surface-ref="{node.ref}"]')
                if action.kind == "click":
                    locator.click(timeout=action.wait.timeout_ms)
                elif action.kind == "type":
                    locator.fill(action.value or "", timeout=action.wait.timeout_ms)
                elif action.kind == "select":
                    locator.select_option(label=action.value, timeout=action.wait.timeout_ms)
            # "read" and "assert" perform no mutation: resolve() above is the
            # whole job (a value is read off resolution.node.value/name by
            # the caller; presence is what "assert" checks). The surface
            # does not interpret whether a value matches — that is business
            # meaning, and belongs to the replay engine's detectors.
        except PlaywrightTimeoutError:
            return self._finish_act(start, ok=False, resolution=resolution, error_code="TIMEOUT", wait=None)
        except PlaywrightError:
            return self._finish_act(
                start, ok=False, resolution=resolution, error_code="NOT_INTERACTABLE", wait=None
            )

        return self._finish_act(start, ok=True, resolution=resolution, error_code=None, wait=action.wait)

    def _finish_act(
        self,
        start: float,
        *,
        ok: bool,
        resolution: Resolution | None,
        error_code: ErrorCode | None,
        wait: WaitSpec | None,
    ) -> ActionResult:
        if wait is not None:
            try:
                self._perform_wait(wait)
            except PlaywrightTimeoutError:
                ok = False
                error_code = "TIMEOUT"
        observation_after = self.observe()
        duration_ms = int((time.monotonic() - start) * 1000)
        return ActionResult(
            ok=ok,
            resolution=resolution,
            error_code=error_code,
            duration_ms=duration_ms,
            observation_after=observation_after,
        )

    def _perform_wait(self, wait: WaitSpec) -> None:
        assert self._page is not None
        if wait.strategy == "load":
            self._page.wait_for_load_state("load", timeout=wait.timeout_ms)
        elif wait.strategy == "condition":
            if wait.condition is None:
                raise ValueError("wait strategy 'condition' requires a condition bundle")
            deadline = time.monotonic() + wait.timeout_ms / 1000
            while time.monotonic() < deadline:
                if self.resolve(wait.condition).status == "resolved":
                    return
                time.sleep(0.1)
            raise PlaywrightTimeoutError(f"condition not met within {wait.timeout_ms}ms")
        else:  # "settle" (default): best-effort quiet period, never a bare sleep(n)
            try:
                self._page.wait_for_load_state("networkidle", timeout=wait.timeout_ms)
            except PlaywrightTimeoutError:
                pass
            time.sleep(0.15)

    # --- evidence --------------------------------------------------------- #

    def capture_screenshot(self, mask: list[Rect] | None) -> bytes:
        assert self._page is not None
        marker_ids: list[str] = []
        if mask:
            for i, rect in enumerate(mask):
                marker = f"surface-mask-{i}"
                self._page.evaluate(
                    """([id, x, y, w, h]) => {
                        const div = document.createElement('div');
                        div.id = id;
                        div.style.position = 'fixed';
                        div.style.left = x + 'px';
                        div.style.top = y + 'px';
                        div.style.width = w + 'px';
                        div.style.height = h + 'px';
                        div.style.background = 'black';
                        div.style.zIndex = 2147483647;
                        document.body.appendChild(div);
                    }""",
                    [marker, rect.x, rect.y, rect.width, rect.height],
                )
                marker_ids.append(marker)
        try:
            return self._page.screenshot(full_page=False)
        finally:
            for marker in marker_ids:
                self._page.evaluate(
                    "(id) => { const el = document.getElementById(id); if (el) el.remove(); }",
                    marker,
                )

    # --- control transfer (docs/escalation-spec.md §3-4) -------------------- #

    def release(self) -> SessionHandle:
        """Cede control to a human. Stops issuing commands (enforced by
        ``act()``'s controller check from here on), flushes the current
        Playwright trace segment, and starts capturing what the human does.
        The browser context is deliberately left open -- nothing here closes
        anything.
        """
        assert self._context is not None and self._page is not None
        assert self._run_id is not None and self._debug_port is not None

        trace_dir = Path("evidence") / self._run_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        self._trace_segment += 1
        self._context.tracing.stop(path=str(trace_dir / f"trace_segment_{self._trace_segment}.zip"))

        self._install_human_capture()
        self._capture_active = True
        self._controller_released = True

        return SessionHandle(
            cdp_endpoint=f"http://127.0.0.1:{self._debug_port}",
            page_url=self._page.url,
            run_id=self._run_id,
            released_at=datetime.now(timezone.utc),
        )

    def reacquire(self, handle: SessionHandle) -> None:
        """Resume control after a human's turn. Reconnects are made real by
        the CDP endpoint (this is the same browser process the whole time --
        never closed), re-observes rather than trusting stale state, resumes
        trace recording as a new segment, and stops recording human actions.
        """
        assert handle.run_id == self._run_id
        assert self._context is not None

        self._capture_active = False
        self._controller_released = False
        self._context.tracing.start(screenshots=True, snapshots=True)
        self.observe()

    # --- human action capture (docs/escalation-spec.md §4) ------------------ #

    def _install_human_capture(self) -> None:
        assert self._context is not None and self._page is not None
        if self._capture_installed:
            return
        self._context.expose_binding("__humanAction", self._on_human_action)
        self._context.add_init_script(_HUMAN_CAPTURE_JS)
        for frame in self._page.frames:
            try:
                frame.evaluate(_HUMAN_CAPTURE_JS)
            except PlaywrightError:
                continue
        self._capture_installed = True

    def pump_events(self) -> None:
        """Deliberately bypasses the controller check (this is bookkeeping,
        not an action): Playwright's sync API only invokes a registered
        callback -- ``expose_binding``'s, here -- when the thread that owns
        this connection makes *another* Playwright call, via the same
        greenlet-switching machinery that call goes through. While a
        session is released, the owning thread is off blocked in
        ``wait_for_handoff``'s plain ``time.sleep``, which never triggers
        that switch, so a human's captured actions would otherwise only
        get written once the engine calls something Playwright-related
        again (``reacquire``) -- arbitrarily late, and specifically too
        late for the hold-timeout clock (docs/escalation-spec.md §1) to see
        that the human is still active. Callers poll and call this once per
        tick for exactly that reason.
        """
        if self._page is None:
            return
        try:
            self._page.evaluate("1")
        except PlaywrightError:
            pass

    def _on_human_action(self, source: dict[str, Any], payload_json: str) -> None:
        # Gated on `_capture_active` rather than uninstalled on reacquire()
        # (Playwright has no clean "remove a binding" call) -- see the
        # comment on _HUMAN_CAPTURE_JS. Either way nothing is written to
        # human_actions.jsonl outside a human's turn.
        if not self._capture_active or self._run_id is None:
            return
        payload = json.loads(payload_json)
        frame: Frame = source["frame"]
        self._human_action_seq += 1
        record = {
            "seq": self._human_action_seq,
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": payload["kind"],
            "role": payload["role"],
            "name": payload["name"],
            "frame_path": self._frame_path_for(frame),
            "url": frame.page.url if frame.page else "",
            "value_redacted": payload.get("value_redacted", False),
        }
        path = Path("evidence") / self._run_id / "human_actions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record))
            f.write("\n")

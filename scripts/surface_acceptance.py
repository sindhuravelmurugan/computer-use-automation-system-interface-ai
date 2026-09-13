"""Acceptance checks for the Surface abstraction (docs/surface-spec.md §7).

Scripted, no LLM. Requires both tenants of the target app already running:

    TENANT=meridian  PORT=5001 python -m target_app
    TENANT=riverbend PORT=5002 python -m target_app

Run with:  python -m scripts.surface_acceptance
"""

from __future__ import annotations

import subprocess
import sys
import time
from io import BytesIO

import requests

from src.schema.common import A11yStrategy, LabelStrategy, SpatialStrategy, Target
from src.surface.types import Action
from src.surface.web import WebSurface

MERIDIAN = "http://127.0.0.1:5001"
RIVERBEND = "http://127.0.0.1:5002"

_RESULTS: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, condition))
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"\n       {detail}"
    print(line)


def bundle(*strategies) -> Target:
    return Target(description="acceptance-script bundle", strategies=list(strategies))


def reset_app(base_url: str) -> None:
    requests.post(f"{base_url}/_test/reset", timeout=5)


def login(surface: WebSurface, base_url: str, run_id: str) -> None:
    surface.open(f"{base_url}/login", run_id=run_id)
    username = bundle(A11yStrategy(role="textbox", name="User name"))
    password = bundle(A11yStrategy(role="textbox", name="Password"))
    signin = bundle(A11yStrategy(role="button", name="Sign In"))
    surface.act(Action(kind="type", target=username, value="agent"))
    surface.act(Action(kind="type", target=password, value="demo"))
    surface.act(Action(kind="click", target=signin))


def mask_is_blanked(unmasked_bytes: bytes, masked_bytes: bytes, rect) -> bool:
    from PIL import Image

    masked_img = Image.open(BytesIO(masked_bytes)).convert("RGB")
    cx = int(rect.x + rect.width / 2)
    cy = int(rect.y + rect.height / 2)
    return masked_img.getpixel((cx, cy)) == (0, 0, 0) and masked_bytes != unmasked_bytes


def no_playwright_under(*dirs: str) -> bool:
    result = subprocess.run(
        ["grep", "-rl", "playwright", *dirs],
        capture_output=True,
        text=True,
    )
    # returncode 0 = a match was found (leak); 1 = no match; 2 = dirs don't
    # exist yet (nothing to leak from, since those modules aren't built yet).
    return result.returncode != 0


def main() -> int:
    reset_app(MERIDIAN)
    reset_app(RIVERBEND)

    member_id_bundle = bundle(A11yStrategy(role="textbox", name="Member ID"))
    search_bundle = bundle(A11yStrategy(role="button", name="Search"))
    savings_bundle = bundle(
        A11yStrategy(role="text", name="Savings Balance"),  # miss: wrong label text
        LabelStrategy(label_text="Savings Balance", control="textbox"),  # miss: not a form control
        SpatialStrategy(anchor_text="Savings", direction="right"),  # hit
    )

    surface = WebSurface(headless=True)
    login(surface, MERIDIAN, run_id="accept-meridian")

    # --- check 1: observe() on search page ------------------------------ #
    obs = surface.observe()
    member_id_box = next(
        (n for n in obs.nodes if n.role == "textbox" and n.name == "Member ID"), None
    )
    search_btn = next((n for n in obs.nodes if n.role == "button" and n.name == "Search"), None)
    check(
        "observe() search page returns Member ID textbox and Search button, both in the iframe",
        member_id_box is not None
        and search_btn is not None
        and len(member_id_box.frame_path) > 1
        and len(search_btn.frame_path) > 1,
        f"member_id_box={member_id_box}\n       search_btn={search_btn}",
    )

    # --- check 2: pruned node count on summary page ---------------------- #
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/10001"))
    obs_summary = surface.observe()
    check(
        "pruned node count is under ~40 on the summary page",
        len(obs_summary.nodes) < 40,
        f"count={len(obs_summary.nodes)}",
    )

    # --- check 3: app_version ---------------------------------------------- #
    check(
        'PageSignature.app_version parses as "v4.2.1"',
        obs_summary.page.app_version == "v4.2.1",
        f"app_version={obs_summary.page.app_version!r}",
    )

    # --- check 4: state_hash stable / changes on navigation --------------- #
    obs_a = surface.observe()
    obs_b = surface.observe()
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/search"))
    obs_c = surface.observe()
    check(
        "state_hash is identical across two consecutive observe() calls with no action between",
        obs_a.state_hash == obs_b.state_hash,
        f"{obs_a.state_hash} vs {obs_b.state_hash}",
    )
    check(
        "state_hash differs after a navigation",
        obs_a.state_hash != obs_c.state_hash,
        f"{obs_a.state_hash} vs {obs_c.state_hash}",
    )

    # --- check 5: state_hash unaffected by typing ------------------------- #
    obs_before_type = surface.observe()
    surface.act(Action(kind="type", target=member_id_bundle, value="1"))
    obs_after_type = surface.observe()
    check(
        "state_hash does not change when a character is typed into a field",
        obs_before_type.state_hash == obs_after_type.state_hash,
        f"{obs_before_type.state_hash} vs {obs_after_type.state_hash}",
    )

    # --- check 6: resolve() a11y rank 0 ------------------------------------ #
    res = surface.resolve(search_bundle)
    check(
        "resolve() finds the Search button by a11y at strategy_index=0",
        res.status == "resolved" and res.strategy_index == 0,
        f"status={res.status}, strategy_index={res.strategy_index}",
    )

    # --- check 7: resolve() spatial fallback -------------------------------- #
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/10001"))
    res = surface.resolve(savings_bundle)
    check(
        'resolve() finds the savings value via spatial (anchor "Savings", direction right) '
        "when a11y and label both miss",
        res.status == "resolved"
        and res.strategy_index == 2
        and res.node is not None
        and res.node.name == "4,832.10",
        f"status={res.status}, strategy_index={res.strategy_index}, node={res.node}",
    )

    # --- check 8: ambiguity, built honestly from a real write flow --------- #
    # Member 10007 already has one "vacation_club" sub-account ("Coast trip").
    # Opening a second one of the same kind produces two genuinely identical
    # "Vacation club" text nodes on the member page -- real ambiguity, not a
    # synthetic fixture.
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/10007"))
    surface.act(Action(kind="click", target=bundle(A11yStrategy(role="link", name="Open sub-account"))))
    surface.act(
        Action(
            kind="select",
            target=bundle(A11yStrategy(role="combobox", name="Sub-account type")),
            value="Vacation club",
        )
    )
    surface.act(
        Action(
            kind="type",
            target=bundle(A11yStrategy(role="textbox", name="Nickname")),
            value="Second trip",
        )
    )
    surface.act(
        Action(
            kind="type",
            target=bundle(A11yStrategy(role="textbox", name="Opening deposit")),
            value="10.00",
        )
    )
    surface.act(
        Action(
            kind="select",
            target=bundle(A11yStrategy(role="combobox", name="Funding source")),
            value="Branch cash deposit",
        )
    )
    surface.act(Action(kind="click", target=bundle(A11yStrategy(role="button", name="Continue to review"))))
    surface.act(Action(kind="click", target=bundle(A11yStrategy(role="button", name="Confirm and open"))))
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/10007"))

    ambiguous_bundle = bundle(A11yStrategy(role="text", name="Vacation club"))
    res = surface.resolve(ambiguous_bundle)
    check(
        "resolve() returns status=ambiguous rather than guessing when a bundle matches multiple nodes",
        res.status == "ambiguous" and res.candidates_found >= 2,
        f"status={res.status}, candidates_found={res.candidates_found}",
    )

    # --- check 9: full scripted sequence ------------------------------------ #
    surface.act(Action(kind="navigate", url=f"{MERIDIAN}/members/search"))
    surface.act(Action(kind="type", target=member_id_bundle, value="10001"))
    click_result = surface.act(Action(kind="click", target=search_bundle))
    res_savings = surface.resolve(savings_bundle)
    check(
        "full sequence (navigate, type 10001, click Search) reaches the summary page and reads 4,832.10",
        click_result.observation_after.page.heading == "Account summary"
        and res_savings.status == "resolved"
        and res_savings.node is not None
        and res_savings.node.name == "4,832.10",
        f"heading={click_result.observation_after.page.heading!r}, savings={res_savings.node}",
    )

    # --- check 10: screenshot masking --------------------------------------- #
    unmasked = surface.capture_screenshot(mask=None)
    masked = surface.capture_screenshot(mask=[res_savings.node.bounds])
    check(
        "capture_screenshot(mask=[...]) writes an image with the masked region blanked",
        mask_is_blanked(unmasked, masked, res_savings.node.bounds),
    )

    surface.close(keep_trace=False)

    # --- check 11: riverbend fails to resolve honestly ----------------------- #
    surface_rb = WebSurface(headless=True)
    login(surface_rb, RIVERBEND, run_id="accept-riverbend")
    surface_rb.act(Action(kind="navigate", url=f"{RIVERBEND}/members/search"))
    type_result = surface_rb.act(Action(kind="type", target=member_id_bundle, value="10001"))
    click_result_rb = surface_rb.act(Action(kind="click", target=search_bundle))
    check(
        "the same bundles replayed against riverbend fail to resolve on the changed "
        "labels and report not_found rather than doing something else",
        (not type_result.ok and type_result.error_code == "NOT_FOUND")
        and (not click_result_rb.ok and click_result_rb.error_code == "NOT_FOUND"),
        f"type: ok={type_result.ok} error={type_result.error_code} "
        f"resolution={type_result.resolution}\n       "
        f"click: ok={click_result_rb.ok} error={click_result_rb.error_code} "
        f"resolution={click_result_rb.resolution}",
    )
    surface_rb.close(keep_trace=False)

    # --- check 12: act() never trusts a stale ref/frame across a delay ------- #
    # Regression test for the frame-caching bug: Frame.child_frames handed
    # back a stale, already-detached iframe object after the search frame
    # navigated past its about:blank placeholder. The interface property
    # this guards is structural: Resolution/UINode carry no live Playwright
    # handles (only strings/floats/lists), and Action.target is typed as a
    # LocatorBundle, not a Resolution -- so act() has no choice but to
    # re-resolve, and re-derive the frame from page.frames, at call time.
    surface_delay = WebSurface(headless=True)
    login(surface_delay, MERIDIAN, run_id="accept-frame-delay")
    surface_delay.act(Action(kind="navigate", url=f"{MERIDIAN}/members/search"))

    # Resolve now, before the search-frame iframe gets torn down and rebuilt.
    early_resolution = surface_delay.resolve(member_id_bundle)
    early_frame = surface_delay._frame_by_path(early_resolution.node.frame_path)

    # Navigate away and back: the iframe is torn down and rebuilt from
    # scratch (a fresh about:blank -> real-src transition), handing
    # Playwright a brand-new Frame object for "frmSearch" -- precisely the
    # scenario that broke a child_frames-based lookup originally.
    surface_delay.act(Action(kind="navigate", url=f"{MERIDIAN}/members/10001"))
    time.sleep(0.5)
    surface_delay.act(Action(kind="navigate", url=f"{MERIDIAN}/members/search"))

    # Proof the earlier frame handle is genuinely gone, not just untested.
    early_frame_detached = early_frame.is_detached()

    # act() only ever receives the bundle, never a Resolution/frame computed
    # earlier, so it must re-resolve and re-derive the frame from
    # page.frames at call time -- and must still succeed here regardless of
    # what happened to any handle from before the rebuild.
    delayed_type = surface_delay.act(Action(kind="type", target=member_id_bundle, value="10001"))
    delayed_click = surface_delay.act(Action(kind="click", target=search_bundle))

    check(
        "acting on a framed element still works after a delay (the iframe "
        "torn down and rebuilt) between resolve() and act()",
        early_frame_detached
        and delayed_type.ok
        and delayed_click.ok
        and delayed_click.observation_after.page.heading == "Account summary",
        f"early_frame_detached={early_frame_detached}, type_ok={delayed_type.ok}, "
        f"click_ok={delayed_click.ok}, heading={delayed_click.observation_after.page.heading!r}",
    )
    surface_delay.close(keep_trace=False)

    # --- check 13: no playwright import outside the surface layer ------------ #
    check(
        "nothing under src/agent/ or src/replay/ imports playwright",
        no_playwright_under("src/agent", "src/replay"),
    )

    print()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

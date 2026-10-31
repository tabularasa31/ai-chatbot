import assert from "node:assert/strict";
import { describe, test } from "node:test";
import {
  LAUNCHER_POINTER_REST,
  launcherVisual,
  reduceLauncherPointer,
  type LauncherPointerInput,
  type LauncherPointerState,
} from "./launcherPointer.ts";

type Step = Partial<LauncherPointerInput> & Pick<LauncherPointerInput, "type">;

function play(steps: Step[]): LauncherPointerState {
  return steps.reduce<LauncherPointerState>(
    (state, step) =>
      reduceLauncherPointer(state, { pointerType: "mouse", button: 0, hoverCapable: true, ...step }),
    LAUNCHER_POINTER_REST,
  );
}

describe("launcher pointer states", () => {
  test("a tap leaves no hover behind", () => {
    const afterTap = play([
      { type: "enter", pointerType: "touch" },
      { type: "down", pointerType: "touch" },
      { type: "up", pointerType: "touch" },
      { type: "leave", pointerType: "touch" },
    ]);
    assert.equal(launcherVisual(afterTap), "rest");
  });

  test("touch shows the pressed state on pointerdown and drops it on pointercancel", () => {
    const pressed = play([{ type: "down", pointerType: "touch" }]);
    assert.equal(launcherVisual(pressed), "pressed");
    const cancelled = play([
      { type: "down", pointerType: "touch" },
      { type: "cancel", pointerType: "touch" },
    ]);
    assert.equal(launcherVisual(cancelled), "rest");
  });

  test("a mouse hovers, presses, returns to hover on release and rests on leave", () => {
    assert.equal(launcherVisual(play([{ type: "enter" }])), "hover");
    assert.equal(launcherVisual(play([{ type: "enter" }, { type: "down" }])), "pressed");
    assert.equal(launcherVisual(play([{ type: "enter" }, { type: "down" }, { type: "up" }])), "hover");
    assert.equal(launcherVisual(play([{ type: "enter" }, { type: "leave" }])), "rest");
  });

  test("a mouse on a device that cannot hover does not hover", () => {
    assert.equal(launcherVisual(play([{ type: "enter", hoverCapable: false }])), "rest");
  });

  test("a secondary mouse button does not press the launcher", () => {
    assert.equal(launcherVisual(play([{ type: "down", button: 2 }])), "rest");
  });

  test("leaving while pressed releases the press", () => {
    assert.equal(launcherVisual(play([{ type: "enter" }, { type: "down" }, { type: "leave" }])), "rest");
  });
});

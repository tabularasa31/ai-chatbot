export type LauncherVisual = "rest" | "hover" | "pressed";

export type LauncherPointerState = { hovered: boolean; pressed: boolean };

export type LauncherPointerInput = {
  type: "enter" | "leave" | "down" | "up" | "cancel";
  pointerType: string;
  button: number;
  hoverCapable: boolean;
};

export const LAUNCHER_POINTER_REST: LauncherPointerState = { hovered: false, pressed: false };

const PRIMARY_BUTTON = 0;

export function reduceLauncherPointer(
  state: LauncherPointerState,
  input: LauncherPointerInput,
): LauncherPointerState {
  switch (input.type) {
    case "enter":
      return { ...state, hovered: input.pointerType === "mouse" && input.hoverCapable };
    case "leave":
      return LAUNCHER_POINTER_REST;
    case "down":
      return { ...state, pressed: input.pointerType !== "mouse" || input.button === PRIMARY_BUTTON };
    case "up":
    case "cancel":
      return { ...state, pressed: false };
  }
}

export function launcherVisual(state: LauncherPointerState): LauncherVisual {
  if (state.pressed) return "pressed";
  return state.hovered ? "hover" : "rest";
}

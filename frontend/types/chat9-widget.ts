// Shared shape of the global Chat9Widget API exposed by widget.js.
// Mirrors the surface defined in apps/widget-loader/src/index.ts.
//
// Kept in /types so dashboard code (DashboardWidgetLoader, DemoBlock) and
// any future consumer can reference one declaration instead of redeclaring
// the type per file.

export type UserHints = {
  user_id?: string;
  email?: string;
  name?: string;
  locale?: string;
  plan_tier?: string;
  audience_tag?: string;
};

export type Chat9StartConfig = {
  userHints?: UserHints;
  mode?: "bubble" | "inline";
  color?: string;
  position?: "right" | "left";
  target?: string;
  topClearance?: number;
  apiBase?: string;
  widgetBase?: string;
};

export type Chat9WidgetApi = {
  start: (config?: Chat9StartConfig) => void;
  stop: () => void;
  setHints: (hints: UserHints | null) => void;
  isStarted: () => boolean;
  destroy: () => void;
};

export type WindowWithChat9Widget = Window & { Chat9Widget?: Chat9WidgetApi };

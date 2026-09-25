// Shared shape of the global Chat9Widget API exposed by widget.js.
// Re-exported from @chat9/widget-shared, the same source apps/widget-app
// uses, so dashboard code (DashboardWidgetLoader, DemoBlock) references one
// declaration instead of redeclaring the type per file.

export type {
  UserHints,
  Chat9StartConfig,
  Chat9WidgetApi,
} from "@chat9/widget-shared";

import type { Chat9WidgetApi } from "@chat9/widget-shared";

export type WindowWithChat9Widget = Window & { Chat9Widget?: Chat9WidgetApi };

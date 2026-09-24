const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";
let authRedirectInProgress = false;

export type TenantResponse = {
  id: string;
  name: string;
  api_key_hint: string | null;
  public_id: string;
  has_openai_key: boolean;
  created_at: string;
  updated_at: string;
};

export type LlmAlertType =
  | "quota_exhausted"
  | "invalid_api_key"
  | "provider_unavailable"
  | "provider_timeout"
  | "rate_limited"
  | "unknown_llm_error";

export type TenantLlmAlertResponse = {
  type: LlmAlertType | null;
  since: string | null;
};

/**
 * The roles this build knows. The API reports `users.role` as a plain string
 * so an unrecognised value degrades rather than 500ing, so treat this as the
 * set worth naming, not a guarantee — every check below tests for "owner"
 * explicitly, which fails closed for anything else.
 */
export type TenantRole = "owner" | "operator";
export type TenantRoleValue = TenantRole | (string & {});

export type TenantMeResponse = TenantResponse & {
  is_admin: boolean;
  is_verified: boolean;
  role: TenantRoleValue;
  /** Whether the caller may operate: answer, take, release, resolve. */
  has_seat: boolean;
};

export type TenantMember = {
  id: string;
  email: string;
  role: TenantRoleValue;
  /** "pending" until the invitee sets a password from their invite link. */
  status: "active" | "pending";
  created_at: string;
  /**
   * When this person's operator seat was granted, or null for no seat.
   * An invite grants one, so in practice only a workspace's founding owner
   * is ever null here.
   */
  seat_granted_at: string | null;
};

export type TenantMemberList = {
  items: TenantMember[];
  /** How many members hold a seat — the figure the seats screen prices. */
  seats: number;
};

export type InviteMemberResponse = {
  member: TenantMember;
};

export type CreateTenantResponse = TenantResponse & {
  api_key: string;
};

export type TenantApiKeyResponse = {
  id: string;
  key_hint: string;
  status: "active" | "revoking" | "revoked";
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  last_used_at: string | null;
};

export type RotateTenantApiKeyResponse = {
  api_key: string;
  key: TenantApiKeyResponse;
  message: string;
};

export type DisclosureLevel = "detailed" | "standard" | "corporate";

export type DisclosureConfigResponse = {
  level: DisclosureLevel;
};

export type SupportSettingsResponse = {
  l2_email: string | null;
  escalation_language: string | null;
  fallback_email: string | null;
};

export type HandoffState = "waiting" | "live" | "bot";

export type InboxTicket = {
  id: string;
  ticket_number: string;
  status: string;
  priority: string;
  trigger: string;
  user_note: string | null;
  resolution_text: string | null;
  created_at: string;
  resolved_at: string | null;
  forwarded_reply_at: string | null;
  forwarded_reply_from: string | null;
};

export type InboxRow = {
  session_id: string;
  chat_id: string;
  handoff_state: HandoffState;
  ticket: InboxTicket | null;
  assigned_operator_id: string | null;
  assigned_operator_email: string | null;
  waiting_since: string | null;
  last_message_role: string | null;
  last_message_preview: string | null;
  last_activity: string;
  message_count: number;
  visitor_email: string | null;
  visitor_name: string | null;
};

export type InboxList = {
  items: InboxRow[];
  waiting_count: number;
  attention_count: number;
};

export type InboxSummary = {
  waiting_count: number;
  attention_count: number;
};

export type OperatorChatState = {
  chat_id: string;
  operator_state: "bot" | "live";
  assigned_operator_id: string | null;
  assigned_operator_email: string | null;
  operator_joined_at: string | null;
  operator_released_at: string | null;
};

export type ThreadMessage = {
  id: string;
  chat_id: string;
  role: "user" | "assistant" | "operator" | (string & {});
  content: string;
  created_at: string;
  author_label: string | null;
};

export type Thread = {
  session_id: string;
  chat: OperatorChatState;
  handoff_state: HandoffState;
  ticket: InboxTicket | null;
  visitor_email: string | null;
  visitor_name: string | null;
  messages: ThreadMessage[];
};

export type BotResponse = {
  id: string;
  tenant_id: string;
  name: string;
  public_id: string;
  is_active: boolean;
  link_safety_enabled: boolean;
  allowed_domains: string[];
  custom_instructions: string | null;
  preset: string | null;
  preset_text: string | null;
  effective_instructions: string | null;
  instructions_source: "preset" | "custom" | "preset+custom" | "none";
  created_at: string;
  updated_at: string;
};

export type AnalyticsPeriod = "7d" | "30d" | "90d";

export type AnalyticsSummaryResponse = {
  period: AnalyticsPeriod;
  from: string;
  to: string;
  messages: number;
  conversations: number;
  deflection_rate: number | null;
  answered_rate: number | null;
  filtered: number;
};

export type AdminMetricsSummary = {
  total_users: number;
  total_tenants: number;
  active_tenants: number;
  total_documents: number;
  total_chat_sessions: number;
  total_messages_user: number;
  total_messages_assistant: number;
  total_tokens_chat: number;
};

export type AdminTenantMetricsItem = {
  tenant_id: string;
  public_id: string;
  owner_email: string | null;
  users_count: number;
  documents_count: number;
  embedded_documents_count: number;
  chat_sessions_count: number;
  messages_user_count: number;
  messages_assistant_count: number;
  tokens_used_chat: number;
  has_openai_key: boolean;
};

export type DocumentHealthWarning = {
  type: string;
  severity: string;
  message: string;
};

export type DocumentHealthStatus = {
  score: number | null;
  checked_at: string;
  warnings: DocumentHealthWarning[];
  error?: string;
};

export type DocumentListItem = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
  created_at: string;
  updated_at: string;
  health_status?: DocumentHealthStatus | null;
};

export type DocumentDetail = {
  id: string;
  filename: string;
  file_type: string;
  status: string;
  source_url: string | null;
  parsed_text: string | null;
  parsed_text_length: number | null;
  created_at: string;
  updated_at: string;
  health_status?: DocumentHealthStatus | null;
};

export type UrlSourceRun = {
  id: string;
  status: string;
  pages_found: number | null;
  pages_indexed: number;
  failed_urls: Array<{ url: string; reason: string }>;
  duration_seconds: number | null;
  error_message?: string | null;
  created_at: string;
  finished_at?: string | null;
};

export type UrlSourcePage = {
  id: string;
  title: string;
  url: string;
  chunk_count: number;
  updated_at: string;
};

export type SourceQuickAnswer = {
  key: string;
  value: string;
  source_url: string;
  detected_at: string;
};

export type UrlSource = {
  id: string;
  name: string;
  url: string;
  source_type: "url";
  status: string;
  schedule: string;
  pages_found: number | null;
  pages_indexed: number;
  chunks_created: number;
  last_crawled_at?: string | null;
  next_crawl_at?: string | null;
  created_at: string;
  updated_at: string;
  warning_message?: string | null;
  error_message?: string | null;
  exclusion_patterns: string[];
};

export type UrlSourceDetail = UrlSource & {
  recent_runs: UrlSourceRun[];
  pages: UrlSourcePage[];
  quick_answers: SourceQuickAnswer[];
};

export type KnowledgeExtractionStatus = "pending" | "done" | "failed";

export type KnowledgeProfile = {
  product_name: string | null;
  topics: string[];
  glossary: Array<{
    term?: string;
    definition?: string | null;
    confidence?: number | null;
    source?: string | null;
  }>;
  support_email: string | null;
  support_urls: string[];
  aliases: Array<Record<string, unknown>>;
  updated_at: string;
  extraction_status: KnowledgeExtractionStatus;
};

export type KnowledgeFaqItem = {
  id: string;
  question: string;
  answer: string;
  confidence: number | null;
  source: string | null;
  approved: boolean;
  created_at: string;
};

export type KnowledgeFaqListResponse = {
  items: KnowledgeFaqItem[];
  total: number;
  pending_count: number;
};

export type GapSource = "mode_a" | "mode_b";
export type GapItemStatus =
  | "active"
  | "closed"
  | "dismissed"
  | "inactive"
  | "drafting"
  | "in_review"
  | "resolved";
export type GapClassification = "uncovered" | "partial" | "covered" | "unknown";
export type GapModeAStatusFilter = "active" | "dismissed" | "archived" | "all";
export type GapModeBStatusFilter =
  | "active"
  | "closed"
  | "dismissed"
  | "inactive"
  | "drafting"
  | "in_review"
  | "resolved"
  | "archived"
  | "all";
export type GapModeASort = "coverage_asc" | "newest";
export type GapModeBSort = "signal_desc" | "coverage_asc" | "newest";
export type GapDismissReason = "feature_request" | "not_relevant" | "already_covered" | "other";
export type GapRunMode = "mode_a" | "mode_b" | "both";

export type GapItem = {
  id: string;
  source: GapSource;
  label: string;
  coverage_score: number | null;
  classification: GapClassification;
  status: GapItemStatus;
  is_new: boolean;
  question_count: number;
  aggregate_signal_weight: number | null;
  example_questions: string[];
  linked_source: GapSource | null;
  linked_label: string | null;
  linked_example_questions: string[];
  also_missing_in_docs: boolean;
  last_updated: string | null;
  has_draft: boolean;
  draft_updated_at: string | null;
  published_faq_id: string | null;
};

export type GapSummary = {
  total_active: number;
  uncovered_count: number;
  partial_count: number;
  impact_statement: string;
  new_badge_count: number;
  last_updated: string | null;
};

export type GapAnalyzerResponse = {
  summary: GapSummary;
  mode_a_items: GapItem[];
  mode_b_items: GapItem[];
};

export type GapSummaryEnvelope = {
  summary: GapSummary;
};

export type GapActionResponse = {
  success: boolean;
  source: GapSource;
  gap_id: string;
  status: GapItemStatus;
};

export type GapDraftResponse = {
  source: GapSource;
  gap_id: string;
  title: string;
  markdown: string;
};

export type GapDraftPayload = {
  gap_id: string;
  title: string;
  question: string;
  markdown: string;
  language: string;
  draft_updated_at: string;
  status: GapItemStatus;
};

export type GapPublishResult = {
  gap_id: string;
  faq_id: string;
  status: GapItemStatus;
};

export type GapDiscardDraftResponse = {
  gap_id: string;
  status: GapItemStatus;
};

export type GapRecalculateResponse = {
  tenant_id: string;
  mode: GapRunMode;
  status: "accepted" | "in_progress" | "rate_limited";
  command_kind: "orchestration";
  http_status_code: 202;
  accepted_at: string | null;
  retry_after_seconds: number | null;
};

function getErrorMessage(data: unknown, fallback: string): string {
  const d = data as { detail?: unknown; message?: string };
  if (typeof d?.detail === "string") return d.detail;
  if (typeof d?.message === "string") return d.message;
  if (Array.isArray(d?.detail)) {
    return d.detail
      .map((item: { msg?: string; message?: string }) => item?.msg ?? item?.message ?? String(item))
      .join(". ");
  }
  return fallback;
}

const SESSION_KEY = "chat9_session";
const SESSION_MAX_AGE_SECONDS = 86400;

/**
 * Same signal `middleware.ts` uses, so the client and the edge never disagree
 * about whether a session exists. A `localStorage` copy would outlive the
 * cookie and send the sign-in screen bouncing off a redirect.
 */
export function hasSession(): boolean {
  if (typeof window === "undefined") return false;
  return document.cookie.split("; ").includes(`${SESSION_KEY}=1`);
}

export function markSession(): void {
  if (typeof window === "undefined") return;
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie = `${SESSION_KEY}=1; path=/; max-age=${SESSION_MAX_AGE_SECONDS}; samesite=lax${secure}`;
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("chat9_access_token");
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie = `${SESSION_KEY}=; path=/; max-age=0; samesite=lax${secure}`;
}

/** Why the sign-in screen is being shown, when we sent the user there on purpose. */
export type SignOutReason = "workspace_deleted";

const SIGN_OUT_REASON_KEY = "chat9_sign_out_reason";

/**
 * Sign out and go to the sign-in screen, on purpose.
 *
 * For the case where losing the session is the *outcome* the user asked for
 * rather than something that went wrong — today, an owner deleting their own
 * workspace, which deletes their account with it.
 *
 * The reason travels in `sessionStorage` rather than a query parameter,
 * because the URL does not survive the trip. Clearing the session cookie makes
 * `middleware.ts` answer the app router's next request for the current route
 * with a redirect to a bare `/login`, and that is the URL the address bar ends
 * up with — losing any query string we set. `sessionStorage` is same-origin
 * and same-tab, so it arrives whichever redirect wins.
 *
 * Claiming `authRedirectInProgress` handles the other half of the same race: a
 * page that just destroyed its own session still has revalidations in flight,
 * and each lands as a 401 that `handleUnauthorized` would answer by
 * redirecting to `/login?error=session_expired` — telling somebody who deleted
 * their workspace deliberately that their session expired.
 */
export function signOutAndLeave(reason: SignOutReason): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(SIGN_OUT_REASON_KEY, reason);
  } catch {
    // Private mode or blocked storage: the sign-in screen just says less.
  }
  clearSession();
  authRedirectInProgress = true;
  window.location.replace("/login");
}

/** Read and consume the reason set by `signOutAndLeave`. Sign-in screen only. */
export function takeSignOutReason(): SignOutReason | null {
  if (typeof window === "undefined") return null;
  try {
    const reason = window.sessionStorage.getItem(SIGN_OUT_REASON_KEY);
    if (reason) window.sessionStorage.removeItem(SIGN_OUT_REASON_KEY);
    return reason === "workspace_deleted" ? reason : null;
  } catch {
    return null;
  }
}

function handleUnauthorized(): void {
  if (typeof window === "undefined") return;

  clearSession();
  fetch(`${BASE_URL}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});

  if (authRedirectInProgress) return;
  authRedirectInProgress = true;

  if (window.location.pathname !== "/login") {
    window.location.replace("/login?error=session_expired");
    return;
  }

  const params = new URLSearchParams(window.location.search);
  if (params.get("error") !== "session_expired") {
    params.set("error", "session_expired");
    const nextUrl = `/login?${params.toString()}`;
    window.location.replace(nextUrl);
  }
}

async function apiFetch(
  url: string,
  options: RequestInit & { skipAuthRedirect?: boolean } = {}
): Promise<Response> {
  const { skipAuthRedirect, ...fetchOptions } = options;
  // credentials must come after the spread so callers cannot accidentally override it
  const response = await fetch(url, { ...fetchOptions, credentials: "include" });

  if (response.status === 401 && !skipAuthRedirect) {
    handleUnauthorized();
  }

  return response;
}

export type AuthUser = { id: string; email: string; created_at: string };
export type AuthSession = { token: string; expires_in: number; user: AuthUser };

async function parseJsonSafe(res: Response): Promise<unknown> {
  return res.json().catch(() => ({}));
}

type RequestInit_ = {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
  skipAuthRedirect?: boolean;
};

/** Fetch → parse JSON (tolerating an empty/204 body) → throw with the server's message on failure. */
async function request<T>(url: string, fallback: string, init: RequestInit_ = {}): Promise<T> {
  const { json, headers, body, ...rest } = init;
  const res = await apiFetch(url, {
    ...rest,
    headers: json !== undefined ? { "Content-Type": "application/json", ...headers } : headers,
    body: json !== undefined ? JSON.stringify(json) : body,
  });
  const data = await parseJsonSafe(res);
  if (!res.ok) throw new Error(getErrorMessage(data, fallback));
  return data as T;
}

const getJson = <T>(url: string, fallback: string): Promise<T> => request<T>(url, fallback);
const sendJson = <T>(url: string, method: string, json: unknown, fallback: string): Promise<T> =>
  request<T>(url, fallback, { method, json });

export const api = {
  auth: {
    register(email: string, password: string): Promise<{ user: AuthUser }> {
      return request(`${BASE_URL}/auth/register`, "Registration failed", {
        method: "POST",
        json: { email, password },
        skipAuthRedirect: true,
      });
    },
    login(email: string, password: string): Promise<AuthSession> {
      return request(`${BASE_URL}/auth/login`, "Login failed", {
        method: "POST",
        json: { email, password },
        skipAuthRedirect: true,
      });
    },
    getMe(): Promise<AuthUser> {
      return getJson(`${BASE_URL}/auth/me`, "Failed to get user");
    },
    verifyEmail(token: string): Promise<AuthSession> {
      return request(`${BASE_URL}/auth/verify-email`, "Failed to verify email", {
        method: "POST",
        json: { token },
        skipAuthRedirect: true,
      });
    },
    forgotPassword(email: string): Promise<{ message: string }> {
      return request(`${BASE_URL}/auth/forgot-password`, "Failed to send reset link", {
        method: "POST",
        json: { email },
        skipAuthRedirect: true,
      });
    },
    resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
      return request(`${BASE_URL}/auth/reset-password`, "Invalid or expired reset link", {
        method: "POST",
        json: { token, new_password: newPassword },
        skipAuthRedirect: true,
      });
    },
    async logout(): Promise<void> {
      await apiFetch(`${BASE_URL}/auth/logout`, {
        method: "POST",
      }).catch(() => {/* ignore network errors on logout */});
    },
  },
  bots: {
    async list(): Promise<BotResponse[]> {
      const data = await getJson<{ items?: BotResponse[] }>(`${BASE_URL}/bots`, "Failed to load bots");
      return data.items ?? [];
    },
    getDisclosure(botId: string): Promise<DisclosureConfigResponse> {
      return getJson(`${BASE_URL}/bots/${botId}/disclosure`, "Failed to load disclosure settings");
    },
    updateDisclosure(botId: string, config: DisclosureConfigResponse): Promise<DisclosureConfigResponse> {
      return sendJson(`${BASE_URL}/bots/${botId}/disclosure`, "PUT", config, "Failed to save disclosure settings");
    },
    update(botId: string, payload: { custom_instructions?: string | null; preset?: string | null; name?: string; is_active?: boolean; link_safety_enabled?: boolean; allowed_domains?: string[] }): Promise<BotResponse> {
      return sendJson(`${BASE_URL}/bots/${botId}`, "PATCH", payload, "Failed to save bot settings");
    },
  },
  clients: {
    getMe(): Promise<TenantMeResponse> {
      return getJson(`${BASE_URL}/tenants/me`, "Failed to get client");
    },
    update(data: { name?: string; openai_api_key?: string | null }): Promise<TenantResponse> {
      return sendJson(`${BASE_URL}/tenants/me`, "PATCH", data, "Failed to update client");
    },
    getLlmAlert(): Promise<TenantLlmAlertResponse> {
      return getJson(`${BASE_URL}/tenants/me/llm-alert`, "Failed to load LLM alert");
    },
    /**
     * Delete the workspace. Owner only, irreversible, and it takes the
     * caller's own account with it — so every later request 401s, including
     * whatever this page would try to refetch. Callers must go straight to
     * /login rather than let that 401 be discovered by a background request.
     *
     * `skipAuthRedirect` is set for that reason: the generic 401 handler
     * bounces to `/login?error=session_expired`, which would tell an owner who
     * just deleted their workspace on purpose that their session expired.
     */
    delete(tenantId: string): Promise<void> {
      return request(`${BASE_URL}/tenants/${tenantId}`, "Failed to delete the workspace", {
        method: "DELETE",
        skipAuthRedirect: true,
      });
    },
  },
  apiKeys: {
    list(): Promise<{ items: TenantApiKeyResponse[] }> {
      return getJson(`${BASE_URL}/tenants/me/api-keys`, "Failed to list API keys");
    },
    rotate(args: {
      reason: "leaked" | "scheduled" | "compromise" | "other";
      revoke_old_immediately: boolean;
    }): Promise<RotateTenantApiKeyResponse> {
      return sendJson(`${BASE_URL}/tenants/me/api-keys/rotate`, "POST", args, "Failed to rotate API key");
    },
    revoke(keyId: string): Promise<TenantApiKeyResponse> {
      return request(`${BASE_URL}/tenants/me/api-keys/${keyId}`, "Failed to revoke API key", { method: "DELETE" });
    },
  },
  members: {
    async list(): Promise<TenantMemberList> {
      const data = await getJson<Partial<TenantMemberList>>(`${BASE_URL}/tenants/members`, "Failed to load team members");
      return { items: data.items ?? [], seats: data.seats ?? 0 };
    },
    /** Always invites an operator: the workspace's one owner created it. */
    invite(email: string): Promise<InviteMemberResponse> {
      return sendJson(`${BASE_URL}/tenants/members/invite`, "POST", { email }, "Failed to send the invite");
    },
    remove(memberId: string): Promise<void> {
      return request(`${BASE_URL}/tenants/members/${memberId}`, "Failed to remove the member", { method: "DELETE" });
    },
    /**
     * Take a seat for yourself. Owner-only, and about the caller alone —
     * everybody else is seated by their invitation.
     */
    takeOwnSeat(): Promise<TenantMember> {
      return request(`${BASE_URL}/tenants/members/me/seat`, "Failed to take a seat", { method: "PUT" });
    },
    /** Give your own seat back. */
    giveUpOwnSeat(): Promise<TenantMember> {
      return request(`${BASE_URL}/tenants/members/me/seat`, "Failed to give up your seat", { method: "DELETE" });
    },
  },
  support: {
    get(): Promise<SupportSettingsResponse> {
      return getJson(`${BASE_URL}/tenants/me/support-settings`, "Failed to load support inbox settings");
    },
    update(config: { l2_email: string | null; escalation_language?: string | null }): Promise<SupportSettingsResponse> {
      return sendJson(`${BASE_URL}/tenants/me/support-settings`, "PUT", config, "Failed to save support inbox settings");
    },
  },
  documents: {
    listSources(): Promise<{ documents: DocumentListItem[]; url_sources: UrlSource[] }> {
      return getJson(`${BASE_URL}/documents/sources`, "Failed to load sources");
    },
    upload(file: File): Promise<{ id: string; filename: string; file_type: string; status: string; created_at: string }> {
      const formData = new FormData();
      formData.append("file", file);
      return request(`${BASE_URL}/documents`, "Failed to upload document", { method: "POST", body: formData });
    },
    createUrlSource(input: {
      url: string;
      name?: string;
      schedule?: string;
      exclusions?: string[];
    }): Promise<UrlSource> {
      return sendJson(`${BASE_URL}/documents/sources/url`, "POST", input, "Failed to create URL source");
    },
    getSourceById(id: string): Promise<UrlSourceDetail> {
      return getJson(`${BASE_URL}/documents/sources/${id}`, "Failed to load source details");
    },
    updateSource(
      id: string,
      input: { name?: string; schedule?: string; exclusions?: string[] }
    ): Promise<UrlSource> {
      return sendJson(`${BASE_URL}/documents/sources/${id}`, "PATCH", input, "Failed to update source");
    },
    refreshSource(id: string): Promise<UrlSource> {
      return request(`${BASE_URL}/documents/sources/${id}/refresh`, "Failed to refresh source", { method: "POST" });
    },
    deleteSource(id: string): Promise<void> {
      return request(`${BASE_URL}/documents/sources/${id}`, "Failed to delete source", { method: "DELETE" });
    },
    deleteSourcePage(sourceId: string, documentId: string): Promise<void> {
      return request(`${BASE_URL}/documents/sources/${sourceId}/pages/${documentId}`, "Failed to delete source page", { method: "DELETE" });
    },
    getById(id: string): Promise<DocumentDetail> {
      return getJson(`${BASE_URL}/documents/${id}`, "Failed to get document");
    },
    delete(id: string): Promise<void> {
      return request(`${BASE_URL}/documents/${id}`, "Failed to delete document", { method: "DELETE" });
    },
    runHealth(docId: string): Promise<DocumentHealthStatus> {
      return request(`${BASE_URL}/documents/${docId}/health/run`, "Health check failed", { method: "POST" });
    },
  },
  knowledge: {
    async getProfile(): Promise<KnowledgeProfile> {
      const data = await getJson<KnowledgeProfile>(`${BASE_URL}/api/v1/knowledge/profile`, "Failed to load knowledge profile");
      return { ...data, topics: Array.isArray(data.topics) ? data.topics : [] };
    },
    patchProfile(
      payload: Partial<Pick<KnowledgeProfile, "product_name" | "topics" | "support_email" | "support_urls">>
    ): Promise<KnowledgeProfile> {
      return sendJson(`${BASE_URL}/api/v1/knowledge/profile`, "PATCH", payload, "Failed to update profile");
    },
    listFaq(params?: {
      approved?: "true" | "false" | "all";
      source?: "docs" | "logs" | "swagger" | "all";
      limit?: number;
      offset?: number;
    }): Promise<KnowledgeFaqListResponse> {
      const search = new URLSearchParams();
      if (params?.approved) search.set("approved", params.approved);
      if (params?.source) search.set("source", params.source);
      if (typeof params?.limit === "number") search.set("limit", String(params.limit));
      if (typeof params?.offset === "number") search.set("offset", String(params.offset));
      const suffix = search.toString() ? `?${search.toString()}` : "";
      return getJson(`${BASE_URL}/api/v1/knowledge/faq${suffix}`, "Failed to load FAQ");
    },
    approveFaq(id: string): Promise<{ id: string; approved: boolean }> {
      return request(`${BASE_URL}/api/v1/knowledge/faq/${id}/approve`, "Failed to approve FAQ", { method: "POST" });
    },
    rejectFaq(id: string): Promise<{ id: string; deleted: boolean }> {
      return request(`${BASE_URL}/api/v1/knowledge/faq/${id}/reject`, "Failed to reject FAQ", { method: "POST" });
    },
    approveAll(): Promise<{ approved_count: number }> {
      return request(`${BASE_URL}/api/v1/knowledge/faq/approve-all`, "Failed to approve all FAQ", { method: "POST" });
    },
    updateFaq(
      id: string,
      payload: { question: string; answer: string }
    ): Promise<KnowledgeFaqItem> {
      return sendJson(`${BASE_URL}/api/v1/knowledge/faq/${id}`, "PUT", payload, "Failed to update FAQ");
    },
  },
  embeddings: {
    create(documentId: string): Promise<{ document_id: string; status: string }> {
      return request(`${BASE_URL}/embeddings/documents/${documentId}`, "Failed to create embeddings", { method: "POST" });
    },
  },
  gapAnalyzer: {
    get(params?: {
      modeAStatus?: GapModeAStatusFilter;
      modeBStatus?: GapModeBStatusFilter;
      modeASort?: GapModeASort;
      modeBSort?: GapModeBSort;
    }): Promise<GapAnalyzerResponse> {
      const search = new URLSearchParams();
      if (params?.modeAStatus) search.set("mode_a_status", params.modeAStatus);
      if (params?.modeBStatus) search.set("mode_b_status", params.modeBStatus);
      if (params?.modeASort) search.set("mode_a_sort", params.modeASort);
      if (params?.modeBSort) search.set("mode_b_sort", params.modeBSort);
      const suffix = search.toString() ? `?${search.toString()}` : "";
      return getJson(`${BASE_URL}/gap-analyzer${suffix}`, "Failed to load Gap Analyzer");
    },
    getSummary(): Promise<GapSummaryEnvelope> {
      return getJson(`${BASE_URL}/gap-analyzer/summary`, "Failed to load Gap Analyzer summary");
    },
    recalculate(mode: GapRunMode): Promise<GapRecalculateResponse> {
      return request(`${BASE_URL}/gap-analyzer/recalculate?mode=${encodeURIComponent(mode)}`, "Failed to start recalculation", { method: "POST" });
    },
    dismiss(
      source: GapSource,
      gapId: string,
      reason: GapDismissReason = "other",
    ): Promise<GapActionResponse> {
      return sendJson(`${BASE_URL}/gap-analyzer/${source}/${gapId}/dismiss`, "POST", { reason }, "Failed to dismiss gap");
    },
    reactivate(source: GapSource, gapId: string): Promise<GapActionResponse> {
      return request(`${BASE_URL}/gap-analyzer/${source}/${gapId}/reactivate`, "Failed to reactivate gap", { method: "POST" });
    },
    draft(source: GapSource, gapId: string): Promise<GapDraftResponse> {
      return request(`${BASE_URL}/gap-analyzer/${source}/${gapId}/draft`, "Failed to generate draft", { method: "POST" });
    },
    generateModeBDraft(gapId: string): Promise<GapDraftPayload> {
      return request(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/draft`, "Failed to generate FAQ draft", { method: "POST" });
    },
    getModeBDraft(gapId: string): Promise<GapDraftPayload> {
      return getJson(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/draft`, "Failed to load draft");
    },
    refineModeBDraft(gapId: string, guidance: string): Promise<GapDraftPayload> {
      return sendJson(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/draft/refine`, "POST", { guidance }, "Failed to refine draft");
    },
    updateModeBDraft(
      gapId: string,
      payload: { title: string; question: string; markdown: string; if_match: string },
    ): Promise<GapDraftPayload> {
      return sendJson(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/draft`, "PATCH", payload, "Failed to save draft");
    },
    discardModeBDraft(gapId: string): Promise<GapDiscardDraftResponse> {
      return request(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/draft`, "Failed to discard draft", { method: "DELETE" });
    },
    publishModeBDraft(gapId: string): Promise<GapPublishResult> {
      return request(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/publish`, "Failed to publish FAQ", { method: "POST" });
    },
    resolveModeBGap(gapId: string): Promise<GapActionResponse> {
      return request(`${BASE_URL}/gap-analyzer/mode_b/${gapId}/resolve`, "Failed to resolve gap", { method: "POST" });
    },
  },
  operator: {
    inbox(scope: "attention" | "all"): Promise<InboxList> {
      return getJson(`${BASE_URL}/operator/inbox?scope=${scope}`, "Failed to load inbox");
    },
    summary(): Promise<InboxSummary> {
      return getJson(`${BASE_URL}/operator/inbox/summary`, "Failed to load inbox summary");
    },
    thread(sessionId: string): Promise<Thread> {
      return getJson(`${BASE_URL}/operator/sessions/${sessionId}`, "Failed to load conversation");
    },
    take(chatId: string): Promise<OperatorChatState> {
      return request(`${BASE_URL}/operator/chats/${chatId}/take`, "Failed to take the chat", { method: "POST" });
    },
    reply(chatId: string, text: string): Promise<{ message_id: string; created_at: string; chat: OperatorChatState }> {
      return sendJson(`${BASE_URL}/operator/chats/${chatId}/messages`, "POST", { text }, "Failed to send the reply");
    },
    release(chatId: string): Promise<OperatorChatState> {
      return request(`${BASE_URL}/operator/chats/${chatId}/release`, "Failed to return the chat to the bot", { method: "POST" });
    },
    resolve(chatId: string, resolutionText?: string | null): Promise<{ chat: OperatorChatState; resolved_ticket_numbers: string[] }> {
      return sendJson(`${BASE_URL}/operator/chats/${chatId}/resolve`, "POST", { resolution_text: resolutionText || null }, "Failed to mark the chat resolved");
    },
  },
  analytics: {
    summary(period: AnalyticsPeriod): Promise<AnalyticsSummaryResponse> {
      return getJson(`${BASE_URL}/analytics/summary?period=${encodeURIComponent(period)}`, "Failed to load analytics summary");
    },
  },
  admin: {
    getSummary(): Promise<AdminMetricsSummary> {
      return getJson(`${BASE_URL}/admin/metrics/summary`, "Failed to load admin metrics summary");
    },
    async getTenants(): Promise<AdminTenantMetricsItem[]> {
      const data = await getJson<{ items?: AdminTenantMetricsItem[] }>(`${BASE_URL}/admin/metrics/tenants`, "Failed to load admin client metrics");
      return data.items ?? [];
    },
  },
};

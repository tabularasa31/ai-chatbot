const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";
let authRedirectInProgress = false;

export type ChatSessionSummary = {
  session_id: string;
  message_count: number;
  last_question: string | null;
  last_answer_preview: string | null;
  last_activity: string;
};

export type MessageFeedbackValue = "up" | "down" | "none";

export type ChatSessionLogs = {
  session_id: string;
  messages: {
    id: string;
    session_id: string;
    role: "user" | "assistant";
    content: string;
    feedback: "none" | "up" | "down";
    ideal_answer: string | null;
    created_at: string;
  }[];
};

export type BadAnswerItem = {
  message_id: string;
  session_id: string;
  question: string | null;
  answer: string;
  ideal_answer: string | null;
  created_at: string;
};

export type ChatDebugResponse = {
  answer: string;
  raw_answer?: string | null;
  tokens_used: number;
  debug: {
    mode: "vector" | "keyword" | "hybrid" | "none";
    best_rank_score: number | null;
    best_confidence_score: number | null;
    confidence_source: "vector_similarity" | "rank_score" | "none" | null;
    strategy?: "faq_direct" | "faq_context" | "rag_only" | "guard_reject" | null;
    reject_reason?:
      | "injection"
      | "not_relevant"
      | "low_retrieval"
      | "insufficient_confidence"
      | null;
    is_reject?: boolean;
    is_faq_direct?: boolean;
    validation_applied?: boolean;
    validation_outcome?: "valid" | "fallback" | "skipped" | null;
    chunks: Array<{
      document_id: string;
      score: number;
      preview: string;
    }>;
  };
};

export type TenantResponse = {
  id: string;
  name: string;
  api_key: string;
  public_id: string;
  has_openai_key: boolean;
  created_at: string;
  updated_at: string;
};

export type TenantMeResponse = TenantResponse & {
  is_admin: boolean;
  is_verified: boolean;
};

export type KycStatusResponse = {
  has_secret: boolean;
  identified_session_rate_7d: number;
  last_identified_session: string | null;
  masked_secret_hint: string | null;
};

export type KycSecretResponse = {
  secret_key: string;
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

export type EscalationTicket = {
  id: string;
  ticket_number: string;
  primary_question: string;
  conversation_summary: string | null;
  trigger: string;
  best_similarity_score: number | null;
  retrieved_chunks_preview: Array<Record<string, unknown>> | null;
  user_id: string | null;
  user_email: string | null;
  user_name: string | null;
  plan_tier: string | null;
  user_note: string | null;
  priority: string;
  status: string;
  resolution_text: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  chat_id: string | null;
  session_id: string | null;
};

export type BotResponse = {
  id: string;
  tenant_id: string;
  name: string;
  public_id: string;
  is_active: boolean;
  agent_instructions: string | null;
  created_at: string;
  updated_at: string;
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
export type GapItemStatus = "active" | "closed" | "dismissed" | "inactive";
export type GapClassification = "uncovered" | "partial" | "covered" | "unknown";
export type GapModeAStatusFilter = "active" | "dismissed" | "archived" | "all";
export type GapModeBStatusFilter = "active" | "closed" | "dismissed" | "inactive" | "archived" | "all";
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

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(SESSION_KEY);
}

export function saveToken(_token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(SESSION_KEY, "1");
}

export function removeToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(SESSION_KEY);
}

function handleUnauthorized(): void {
  if (typeof window === "undefined") return;

  removeToken();
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

async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, { ...options, credentials: "include" });

  if (response.status === 401) {
    handleUnauthorized();
  }

  return response;
}

export const api = {
  auth: {
    async register(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Registration failed"));
      return data as { user: { id: string; email: string; created_at: string } };
    },
    async login(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Login failed"));
      return data as { token: string; expires_in: number; user: { id: string; email: string; created_at: string } };
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/auth/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get user"));
      return data as { id: string; email: string; created_at: string };
    },
    async verifyEmail(token: string): Promise<{ token: string; expires_in: number; user: { id: string; email: string; created_at: string } }> {
      const res = await fetch(`${BASE_URL}/auth/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to verify email"));
      return data;
    },
    async forgotPassword(email: string): Promise<{ message: string }> {
      const res = await fetch(`${BASE_URL}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to send reset link"));
      return data as { message: string };
    },
    async resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
      const res = await fetch(`${BASE_URL}/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: newPassword }),
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Invalid or expired reset link"));
      return data as { message: string };
    },
    async logout(): Promise<void> {
      await fetch(`${BASE_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
      }).catch(() => {/* ignore network errors on logout */});
    },
  },
  bots: {
    async list(): Promise<BotResponse[]> {
      const res = await authFetch(`${BASE_URL}/bots`);
      if (!res.ok) throw new Error("Failed to load bots");
      const data = await res.json();
      return (data.items ?? []) as BotResponse[];
    },
    async getDisclosure(botId: string): Promise<DisclosureConfigResponse> {
      const res = await authFetch(`${BASE_URL}/bots/${botId}/disclosure`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load disclosure settings"));
      return data as DisclosureConfigResponse;
    },
    async updateDisclosure(botId: string, config: DisclosureConfigResponse): Promise<DisclosureConfigResponse> {
      const res = await authFetch(`${BASE_URL}/bots/${botId}/disclosure`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to save disclosure settings"));
      return data as DisclosureConfigResponse;
    },
    async update(botId: string, payload: { agent_instructions?: string | null; name?: string; is_active?: boolean }): Promise<BotResponse> {
      const res = await authFetch(`${BASE_URL}/bots/${botId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to save bot settings"));
      return data as BotResponse;
    },
  },
  clients: {
    async create(name: string) {
      const res = await authFetch(`${BASE_URL}/tenants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create client"));
      return data as TenantResponse;
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/tenants/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get client"));
      return data as TenantMeResponse;
    },
    async update(data: { name?: string; openai_api_key?: string | null }) {
      const res = await authFetch(`${BASE_URL}/tenants/me`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const responseData = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(responseData, "Failed to update client"));
      return responseData as TenantResponse;
    },
  },
  kyc: {
    async generateSecret(): Promise<KycSecretResponse> {
      const res = await authFetch(`${BASE_URL}/tenants/me/kyc/secret`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to generate KYC secret"));
      return data as KycSecretResponse;
    },
    async getStatus(): Promise<KycStatusResponse> {
      const res = await authFetch(`${BASE_URL}/tenants/me/kyc/status`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get KYC status"));
      return data as KycStatusResponse;
    },
    async rotateSecret(): Promise<KycSecretResponse> {
      const res = await authFetch(`${BASE_URL}/tenants/me/kyc/rotate`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to rotate KYC secret"));
      return data as KycSecretResponse;
    },
  },
  support: {
    async get(): Promise<SupportSettingsResponse> {
      const res = await authFetch(`${BASE_URL}/tenants/me/support-settings`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load support inbox settings"));
      return data as SupportSettingsResponse;
    },
    async update(config: { l2_email: string | null; escalation_language?: string | null }): Promise<SupportSettingsResponse> {
      const res = await authFetch(`${BASE_URL}/tenants/me/support-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to save support inbox settings"));
      return data as SupportSettingsResponse;
    },
  },
  documents: {
    async list() {
      const res = await authFetch(`${BASE_URL}/documents`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to list documents"));
      const list = data as {
        documents: Array<{
          id: string;
          filename: string;
          file_type: string;
          status: string;
          created_at: string;
          updated_at: string;
          health_status?: DocumentHealthStatus | null;
        }>;
      };
      return list.documents;
    },
    async listSources(): Promise<{ documents: DocumentListItem[]; url_sources: UrlSource[] }> {
      const res = await authFetch(`${BASE_URL}/documents/sources`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load sources"));
      return data as { documents: DocumentListItem[]; url_sources: UrlSource[] };
    },
    async upload(file: File) {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${BASE_URL}/documents`, {
        method: "POST",
        credentials: "include",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to upload document"));
      return data as {
        id: string;
        filename: string;
        file_type: string;
        status: string;
        created_at: string;
      };
    },
    async createUrlSource(input: {
      url: string;
      name?: string;
      schedule?: string;
      exclusions?: string[];
    }): Promise<UrlSource> {
      const res = await authFetch(`${BASE_URL}/documents/sources/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create URL source"));
      return data as UrlSource;
    },
    async getSourceById(id: string): Promise<UrlSourceDetail> {
      const res = await authFetch(`${BASE_URL}/documents/sources/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load source details"));
      return data as UrlSourceDetail;
    },
    async updateSource(
      id: string,
      input: { name?: string; schedule?: string; exclusions?: string[] }
    ): Promise<UrlSource> {
      const res = await authFetch(`${BASE_URL}/documents/sources/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to update source"));
      return data as UrlSource;
    },
    async refreshSource(id: string): Promise<UrlSource> {
      const res = await authFetch(`${BASE_URL}/documents/sources/${id}/refresh`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to refresh source"));
      return data as UrlSource;
    },
    async deleteSource(id: string): Promise<void> {
      const res = await authFetch(`${BASE_URL}/documents/sources/${id}`, { method: "DELETE" });
      if (res.status !== 204 && !res.ok) {
        const data = await res.json();
        throw new Error(getErrorMessage(data, "Failed to delete source"));
      }
    },
    async deleteSourcePage(sourceId: string, documentId: string): Promise<void> {
      const res = await authFetch(`${BASE_URL}/documents/sources/${sourceId}/pages/${documentId}`, {
        method: "DELETE",
      });
      if (res.status !== 204 && !res.ok) {
        const data = await res.json();
        throw new Error(getErrorMessage(data, "Failed to delete source page"));
      }
    },
    async getById(id: string) {
      const res = await authFetch(`${BASE_URL}/documents/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get document"));
      return data as {
        id: string;
        filename: string;
        file_type: string;
        status: string;
        created_at: string;
        updated_at: string;
        health_status?: DocumentHealthStatus | null;
      };
    },
    async delete(id: string) {
      const res = await authFetch(`${BASE_URL}/documents/${id}`, { method: "DELETE" });
      if (res.status !== 204 && !res.ok) {
        const data = await res.json();
        throw new Error(getErrorMessage(data, "Failed to delete document"));
      }
    },
    async getHealth(docId: string): Promise<DocumentHealthStatus> {
      const res = await authFetch(`${BASE_URL}/documents/${docId}/health`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Health check not available"));
      return data as DocumentHealthStatus;
    },
    async runHealth(docId: string): Promise<DocumentHealthStatus> {
      const res = await authFetch(`${BASE_URL}/documents/${docId}/health/run`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Health check failed"));
      return data as DocumentHealthStatus;
    },
  },
  knowledge: {
    async getProfile(botId?: string): Promise<KnowledgeProfile> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/profile`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load knowledge profile"));
      return {
        ...data,
        topics: Array.isArray(data.topics) ? data.topics : [],
      } as KnowledgeProfile;
    },
    async patchProfile(
      payload: Partial<Pick<KnowledgeProfile, "product_name" | "topics" | "support_email" | "support_urls">>,
      botId?: string
    ): Promise<KnowledgeProfile> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/profile`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to update profile"));
      return data as KnowledgeProfile;
    },
    async listFaq(params?: {
      approved?: "true" | "false" | "all";
      source?: "docs" | "logs" | "swagger" | "all";
      limit?: number;
      offset?: number;
    }, botId?: string): Promise<KnowledgeFaqListResponse> {
      const search = new URLSearchParams();
      if (params?.approved) search.set("approved", params.approved);
      if (params?.source) search.set("source", params.source);
      if (typeof params?.limit === "number") search.set("limit", String(params.limit));
      if (typeof params?.offset === "number") search.set("offset", String(params.offset));
      const suffix = search.toString() ? `?${search.toString()}` : "";
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq${suffix}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load FAQ"));
      return data as KnowledgeFaqListResponse;
    },
    async approveFaq(id: string, botId?: string): Promise<{ id: string; approved: boolean }> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq/${id}/approve`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to approve FAQ"));
      return data as { id: string; approved: boolean };
    },
    async rejectFaq(id: string, botId?: string): Promise<{ id: string; deleted: boolean }> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq/${id}/reject`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to reject FAQ"));
      return data as { id: string; deleted: boolean };
    },
    async approveAll(botId?: string): Promise<{ approved_count: number }> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq/approve-all`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to approve all FAQ"));
      return data as { approved_count: number };
    },
    async updateFaq(
      id: string,
      payload: { question: string; answer: string },
      botId?: string
    ): Promise<KnowledgeFaqItem> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to update FAQ"));
      return data as KnowledgeFaqItem;
    },
    async deleteFaq(id: string, botId?: string): Promise<{ id: string; deleted: boolean }> {
      const base = botId ? `${BASE_URL}/api/v1/tenants/${encodeURIComponent(botId)}/knowledge` : `${BASE_URL}/knowledge`;
      const res = await authFetch(`${base}/faq/${id}`, {
        method: "DELETE",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to delete FAQ"));
      return data as { id: string; deleted: boolean };
    },
  },
  embeddings: {
    async create(documentId: string) {
      const res = await authFetch(`${BASE_URL}/embeddings/documents/${documentId}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create embeddings"));
      return data as { document_id: string; status: string };
    },
  },
  gapAnalyzer: {
    async get(params?: {
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
      const res = await authFetch(`${BASE_URL}/gap-analyzer${suffix}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load Gap Analyzer"));
      return data as GapAnalyzerResponse;
    },
    async getSummary(): Promise<GapSummaryEnvelope> {
      const res = await authFetch(`${BASE_URL}/gap-analyzer/summary`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load Gap Analyzer summary"));
      return data as GapSummaryEnvelope;
    },
    async recalculate(mode: GapRunMode): Promise<GapRecalculateResponse> {
      const res = await authFetch(`${BASE_URL}/gap-analyzer/recalculate?mode=${encodeURIComponent(mode)}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to start recalculation"));
      return data as GapRecalculateResponse;
    },
    async dismiss(
      source: GapSource,
      gapId: string,
      reason: GapDismissReason = "other",
    ): Promise<GapActionResponse> {
      const res = await authFetch(`${BASE_URL}/gap-analyzer/${source}/${gapId}/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to dismiss gap"));
      return data as GapActionResponse;
    },
    async reactivate(source: GapSource, gapId: string): Promise<GapActionResponse> {
      const res = await authFetch(`${BASE_URL}/gap-analyzer/${source}/${gapId}/reactivate`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to reactivate gap"));
      return data as GapActionResponse;
    },
    async draft(source: GapSource, gapId: string): Promise<GapDraftResponse> {
      const res = await authFetch(`${BASE_URL}/gap-analyzer/${source}/${gapId}/draft`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to generate draft"));
      return data as GapDraftResponse;
    },
  },
  chat: {
    async listSessions(): Promise<ChatSessionSummary[]> {
      const res = await authFetch(`${BASE_URL}/chat/sessions`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to list sessions"));
      const list = data as { sessions: ChatSessionSummary[] };
      return list.sessions;
    },
    async getSessionLogs(sessionId: string): Promise<ChatSessionLogs> {
      const res = await authFetch(`${BASE_URL}/chat/logs/session/${sessionId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get session logs"));
      const log = data as { messages: ChatSessionLogs["messages"] };
      return { session_id: sessionId, messages: log.messages };
    },
    async setFeedback(
      messageId: string,
      feedback: MessageFeedbackValue,
      idealAnswer?: string | null
    ) {
      const res = await authFetch(`${BASE_URL}/chat/messages/${messageId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback, ideal_answer: idealAnswer ?? null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to set feedback"));
      return data as { id: string; feedback: string; ideal_answer: string | null };
    },
    async listBadAnswers(limit = 50, offset = 0): Promise<BadAnswerItem[]> {
      const res = await authFetch(
        `${BASE_URL}/chat/bad-answers?limit=${limit}&offset=${offset}`
      );
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to list bad answers"));
      const list = data as { items: BadAnswerItem[] };
      return list.items;
    },
    async send(
      question: string,
      apiKey: string,
      sessionId?: string,
      options?: { browserLocale?: string | null }
    ) {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      };
      if (options?.browserLocale) {
        headers["X-Browser-Locale"] = options.browserLocale;
      }
      const body: { question: string; session_id?: string } = { question };
      if (sessionId) body.session_id = sessionId;
      const res = await fetch(`${BASE_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Chat failed"));
      return data as {
        text: string;
        session_id: string;
        chat_ended?: boolean;
        ticket_number?: string | null;
        source_documents?: string[] | null;
        tokens_used?: number | null;
      };
    },
    async manualEscalate(
      apiKey: string,
      sessionId: string,
      body?: { user_note?: string | null; trigger?: "user_request" | "answer_rejected" }
    ) {
      const res = await fetch(`${BASE_URL}/chat/${sessionId}/escalate`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        body: JSON.stringify({
          user_note: body?.user_note ?? null,
          trigger: body?.trigger ?? "user_request",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Escalation failed"));
      return data as { message: string; ticket_number: string };
    },
    async getHistory(sessionId: string) {
      const res = await authFetch(`${BASE_URL}/chat/history/${sessionId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get history"));
      return data as {
        session_id: string;
        messages: Array<{
          id: number;
          role: string;
          content: string;
          created_at: string;
        }>;
      };
    },
    async debug(question: string, botId: string): Promise<ChatDebugResponse> {
      const q = `?bot_id=${encodeURIComponent(botId.trim())}`;
      const res = await authFetch(`${BASE_URL}/chat/debug${q}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Debug failed"));
      return data as ChatDebugResponse;
    },
  },
  escalations: {
    async list(params?: { status?: string }): Promise<EscalationTicket[]> {
      const q = params?.status
        ? `?status=${encodeURIComponent(params.status)}`
        : "";
      const res = await authFetch(`${BASE_URL}/escalations${q}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to list tickets"));
      const list = data as { tickets: EscalationTicket[] };
      return list.tickets;
    },
    async get(id: string): Promise<EscalationTicket> {
      const res = await authFetch(`${BASE_URL}/escalations/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load ticket"));
      return data as EscalationTicket;
    },
    async resolve(id: string, resolutionText: string): Promise<EscalationTicket> {
      const res = await authFetch(`${BASE_URL}/escalations/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution_text: resolutionText }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to resolve ticket"));
      return data as EscalationTicket;
    },
  },
  admin: {
    async getSummary(): Promise<AdminMetricsSummary> {
      const res = await authFetch(`${BASE_URL}/admin/metrics/summary`);
      if (!res.ok) throw new Error("Failed to load admin metrics summary");
      return res.json();
    },
    async getTenants(): Promise<AdminTenantMetricsItem[]> {
      const res = await authFetch(`${BASE_URL}/admin/metrics/tenants`);
      if (!res.ok) throw new Error("Failed to load admin client metrics");
      const data = await res.json();
      return data.items;
    },
  },
};

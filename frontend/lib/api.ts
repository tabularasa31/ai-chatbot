const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

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
  tokens_used: number;
  debug: {
    mode: "vector" | "keyword" | "hybrid" | "none";
    chunks: Array<{
      document_id: string;
      score: number;
      preview: string;
    }>;
  };
};

export type ClientResponse = {
  id: string;
  name: string;
  api_key: string;
  public_id: string;
  has_openai_key: boolean;
  created_at: string;
  updated_at: string;
};

export type ClientMeResponse = ClientResponse & {
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

export type AdminMetricsSummary = {
  total_users: number;
  total_clients: number;
  active_clients: number;
  total_documents: number;
  total_chat_sessions: number;
  total_messages_user: number;
  total_messages_assistant: number;
  total_tokens_chat: number;
};

export type AdminClientMetricsItem = {
  client_id: string;
  name: string;
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

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

export function saveToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem("token", token);
  document.cookie = `token=${token}; path=/; max-age=86400; samesite=lax`;
}

export function removeToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem("token");
  document.cookie = "token=; path=/; max-age=0";
}

async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers: HeadersInit = {
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers });
}

export const api = {
  auth: {
    async register(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Registration failed"));
      return data as { token: string; expires_in: number; user: { id: number; email: string } };
    },
    async login(email: string, password: string) {
      const res = await fetch(`${BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Login failed"));
      return data as { token: string; expires_in: number; user: { id: number; email: string } };
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/auth/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get user"));
      return data as { id: string; email: string; created_at: string };
    },
    async verifyEmail(token: string): Promise<{ status: string }> {
      const res = await fetch(`${BASE_URL}/auth/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          (err as { detail?: string }).detail ?? "Failed to verify email"
        );
      }
      return res.json();
    },
    async forgotPassword(email: string): Promise<{ message: string }> {
      const res = await fetch(`${BASE_URL}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
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
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Invalid or expired reset link"));
      return data as { message: string };
    },
  },
  clients: {
    async create(name: string) {
      const res = await authFetch(`${BASE_URL}/clients`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create client"));
      return data as ClientResponse;
    },
    async getMe() {
      const res = await authFetch(`${BASE_URL}/clients/me`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get client"));
      return data as ClientMeResponse;
    },
    async update(data: { name?: string; openai_api_key?: string | null }) {
      const res = await authFetch(`${BASE_URL}/clients/me`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const responseData = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(responseData, "Failed to update client"));
      return responseData as ClientResponse;
    },
  },
  kyc: {
    async generateSecret(): Promise<KycSecretResponse> {
      const res = await authFetch(`${BASE_URL}/clients/me/kyc/secret`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to generate KYC secret"));
      return data as KycSecretResponse;
    },
    async getStatus(): Promise<KycStatusResponse> {
      const res = await authFetch(`${BASE_URL}/clients/me/kyc/status`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to get KYC status"));
      return data as KycStatusResponse;
    },
    async rotateSecret(): Promise<KycSecretResponse> {
      const res = await authFetch(`${BASE_URL}/clients/me/kyc/rotate`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to rotate KYC secret"));
      return data as KycSecretResponse;
    },
  },
  disclosure: {
    async get(): Promise<DisclosureConfigResponse> {
      const res = await authFetch(`${BASE_URL}/clients/me/disclosure`);
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to load disclosure settings"));
      return data as DisclosureConfigResponse;
    },
    async update(config: DisclosureConfigResponse): Promise<DisclosureConfigResponse> {
      const res = await authFetch(`${BASE_URL}/clients/me/disclosure`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to save disclosure settings"));
      return data as DisclosureConfigResponse;
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
    async upload(file: File) {
      const formData = new FormData();
      formData.append("file", file);
      const token = getToken();
      const res = await fetch(`${BASE_URL}/documents`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
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
  embeddings: {
    async create(documentId: string) {
      const res = await authFetch(`${BASE_URL}/embeddings/documents/${documentId}`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) throw new Error(getErrorMessage(data, "Failed to create embeddings"));
      return data as { document_id: string; chunks_created: number; status: string };
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
        answer: string;
        session_id: string;
        source_documents?: string[];
        tokens_used?: number;
        chat_ended?: boolean;
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
    async debug(question: string): Promise<ChatDebugResponse> {
      const res = await authFetch(`${BASE_URL}/chat/debug`, {
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
    async getClients(): Promise<AdminClientMetricsItem[]> {
      const res = await authFetch(`${BASE_URL}/admin/metrics/clients`);
      if (!res.ok) throw new Error("Failed to load admin client metrics");
      const data = await res.json();
      return data.items;
    },
  },
};

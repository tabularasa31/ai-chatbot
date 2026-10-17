import { ApiError, HTTP_BAD_REQUEST, HTTP_CONFLICT, HTTP_UNPROCESSABLE } from "@/lib/api";

const SERVER_REJECTION_STATUSES = [HTTP_BAD_REQUEST, HTTP_CONFLICT, HTTP_UNPROCESSABLE];

/** The text of a 400, 409 or 422 is written for the user; any other failure is ours and stays unprinted. */
export function serverRejection(err: unknown): string | null {
  return err instanceof ApiError && SERVER_REJECTION_STATUSES.includes(err.status) ? err.message : null;
}

export function saveErrorText(err: unknown, fallback: string): string {
  return serverRejection(err) ?? fallback;
}

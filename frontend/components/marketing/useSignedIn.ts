import { useSyncExternalStore } from "react";
import { hasSession } from "@/lib/api";

const subscribeNever = () => () => {};
const signedOutOnServer = () => false;

export function useSignedIn(): boolean {
  return useSyncExternalStore(subscribeNever, hasSession, signedOutOnServer);
}

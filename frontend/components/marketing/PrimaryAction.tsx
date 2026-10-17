"use client";

import Link from "next/link";
import { buttonClasses } from "@/components/ui/button";
import { useSignedIn } from "./useSignedIn";

export function PrimaryAction() {
  const signedIn = useSignedIn();

  return (
    <Link href={signedIn ? "/dashboard" : "/signup"} className={buttonClasses({ variant: "primary" })}>
      {signedIn ? "Open dashboard" : "Create a free account"}
    </Link>
  );
}

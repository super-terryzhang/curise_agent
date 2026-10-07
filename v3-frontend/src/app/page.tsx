"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { STANDALONE_DATA_PREPARATION } from "@/lib/data-preparation-routes";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    if (STANDALONE_DATA_PREPARATION) { router.replace("/prepare"); return; }
    if (isAuthenticated()) {
      router.replace("/dashboard");
    } else {
      router.replace("/login");
    }
  }, [router]);

  return null;
}

"use client";

import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { Listing } from "@/lib/types";

export default function Trash() {
  const load = useCallback(() => api<Listing>("/drive/trash"), []);
  return <Browser mode="trash" title="Trash" load={load} empty={{ title: "The trash is empty", hint: "Deleted files wait here for 30 days." }} />;
}

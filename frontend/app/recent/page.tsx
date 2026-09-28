"use client";

import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { FileItem, Listing } from "@/lib/types";

export default function Recent() {
  const load = useCallback(async (): Promise<Listing> => {
    const files = await api<FileItem[]>("/drive/recent");
    return { folder: null, breadcrumbs: [], folders: [], files };
  }, []);
  return <Browser mode="flat" title="Recent" load={load} empty={{ title: "Nothing yet", hint: "Files you upload or open show up here." }} />;
}

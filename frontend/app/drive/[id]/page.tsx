"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { Listing } from "@/lib/types";

export default function FolderPage() {
  const { id } = useParams<{ id: string }>();
  const load = useCallback(() => api<Listing>(`/drive/folders/${id}`), [id]);
  return (
    <Browser
      mode="folder"
      folderId={id}
      load={load}
      empty={{ title: "This folder is empty", hint: "Drag files or folders here, or use New → Upload." }}
    />
  );
}

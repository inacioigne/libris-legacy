"use client";

import { useRouter } from "next/navigation";
import { AuthorityRelations } from "./AuthorityRelations";

export function ConnectedAuthorityRelations(props: Parameters<typeof AuthorityRelations>[0]) {
  const router = useRouter();
  return <AuthorityRelations {...props} onSaved={() => router.refresh()} />;
}

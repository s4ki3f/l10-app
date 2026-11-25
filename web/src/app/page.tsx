"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/lib/queries";

/** Home: signed in → your first team; otherwise → sign in. */
export default function Home() {
  const router = useRouter();
  const me = useMe();
  useEffect(() => {
    if (me.isError) router.replace("/login");
    else if (me.data) router.replace(me.data.teams[0] ? `/teams/${me.data.teams[0].id}` : "/login");
  }, [me.data, me.isError, router]);
  return <div className="p-8"><Skeleton className="h-8 w-48" /></div>;
}

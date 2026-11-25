"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { roleLabel } from "@/components/role";
import { call, useSignInUsers } from "@/lib/queries";

/** The seeded user switcher (sign-up is out of scope). */
export default function LoginPage() {
  const users = useSignInUsers();
  const router = useRouter();
  const qc = useQueryClient();

  async function signIn(id: string) {
    await call.login(id);
    qc.clear(); // nothing cached from a previous user survives
    router.replace("/");
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md items-center px-4">
      <Card className="w-full">
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Pick a seeded user. Each browser can be a different person.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          {users.isLoading && Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-10" />)}
          {users.data?.map((u) => (
            <Button key={u.id} variant="outline" className="h-auto justify-between py-2" onClick={() => signIn(u.id)}
                    data-testid={`signin-${u.id}`}>
              <span>{u.name}</span>
              <Badge variant={u.role === "observer" ? "secondary" : "outline"}>{roleLabel(u.role)}</Badge>
            </Button>
          ))}
        </CardContent>
      </Card>
    </main>
  );
}

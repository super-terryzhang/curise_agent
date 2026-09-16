"use client";

/**
 * LINE bind page — `/line/bind?token=xyz`.
 *
 * Users get here after tapping a one-shot bind URL the LINE bot sent them.
 * They enter the email + password of their company account; we POST to
 * /api/line/bind/{token}, which:
 *  - verifies the password (delegates to identity_service.login),
 *  - consumes the token (single-use, ~30 min TTL),
 *  - writes a LineUser row binding (line_user_id, channel) → internal user.
 *
 * On success the page does NOT log the user into the web app — the user
 * is signing in *to LINE*, not the dashboard. We just show a confirmation
 * and tell them to switch back to LINE.
 *
 * Token-missing / token-bad / 409 hijack states each have their own visible
 * copy so the user knows whether to retry-from-LINE or contact admin.
 */

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { bindLine, BindError } from "@/lib/line-bind-api";

function BindForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState<{ email: string; role: string } | null>(null);
  // `tokenError` blocks the form before the user even tries — for the
  // missing-token URL case. Backend errors during submit go through `toast`.
  const [tokenError, setTokenError] = useState<string>("");
  // After a 400 from the backend, lock the form and tell the user to go back
  // to LINE for a fresh link. Retrying with the same token will keep failing.
  const [terminal, setTerminal] = useState<{ title: string; body: string } | null>(null);

  useEffect(() => {
    if (!token) {
      setTokenError("缺少绑定令牌。请回到 LINE，向机器人发送任意消息以获取新的绑定链接。");
    }
  }, [token]);

  const emailErr = submitAttempted
    ? !email.trim()
      ? "请输入邮箱地址"
      : !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
      ? "请输入有效的邮箱格式"
      : ""
    : "";
  const passwordErr = submitAttempted && !password ? "请输入密码" : "";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitAttempted(true);
    if (!token || !email.trim() || !password || emailErr) return;
    setLoading(true);
    try {
      const res = await bindLine(token, { email, password });
      setSuccess({ email: res.user_email, role: res.user_role });
    } catch (err) {
      if (err instanceof BindError) {
        if (err.code === "invalid_token") {
          // One-shot tokens stay invalid forever; lock the form.
          setTerminal({
            title: "绑定链接已失效",
            body: err.message,
          });
        } else {
          toast.error(err.message);
        }
      } else {
        toast.error("绑定失败，请稍后再试");
      }
    } finally {
      setLoading(false);
    }
  };

  // ─── Success state ────────────────────────────────────────────

  if (success) {
    return (
      <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
        <CardContent className="pt-6 pb-6 text-center space-y-4">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-emerald-500/10">
            <CheckCircle2 className="h-6 w-6 text-emerald-600" />
          </div>
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">绑定成功</h2>
            <p className="text-sm text-muted-foreground">
              您的 LINE 账号已绑定到 <span className="font-medium text-foreground">{success.email}</span>
            </p>
          </div>
          <p className="text-xs text-muted-foreground leading-relaxed">
            请回到 LINE，向机器人发送您的问题即可使用。
            <br />
            您可以关闭此页面。
          </p>
        </CardContent>
      </Card>
    );
  }

  // ─── Terminal-error state (invalid / used / expired token) ───

  if (terminal) {
    return (
      <Card className="border-destructive/40 bg-card/50 backdrop-blur-sm">
        <CardContent className="pt-6 pb-6 text-center space-y-4">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-destructive/10">
            <AlertCircle className="h-6 w-6 text-destructive" />
          </div>
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">{terminal.title}</h2>
            <p className="text-sm text-muted-foreground">{terminal.body}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // ─── Token-missing pre-flight ────────────────────────────────

  if (tokenError) {
    return (
      <Card className="border-destructive/40 bg-card/50 backdrop-blur-sm">
        <CardContent className="pt-6 pb-6 text-center space-y-3">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-destructive/10">
            <AlertCircle className="h-6 w-6 text-destructive" />
          </div>
          <h2 className="text-lg font-semibold">无法继续</h2>
          <p className="text-sm text-muted-foreground">{tokenError}</p>
        </CardContent>
      </Card>
    );
  }

  // ─── Form ────────────────────────────────────────────────────

  return (
    <Card className="border-border/50 bg-card/50 backdrop-blur-sm">
      <CardContent className="pt-6">
        <div className="mb-4 space-y-1">
          <h2 className="text-base font-semibold">绑定 LINE 账号</h2>
          <p className="text-xs text-muted-foreground">
            请使用您的公司账号登录以完成绑定。绑定后即可在 LINE 中使用本助手。
          </p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">公司邮箱</Label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@company.com"
              autoComplete="email"
              autoFocus
              aria-invalid={emailErr ? true : undefined}
            />
            {emailErr && <p className="text-xs text-destructive">{emailErr}</p>}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">密码</Label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入密码"
              autoComplete="current-password"
              aria-invalid={passwordErr ? true : undefined}
            />
            {passwordErr && <p className="text-xs text-destructive">{passwordErr}</p>}
          </div>
          <Button type="submit" className="w-full" disabled={loading}>
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {loading ? "绑定中..." : "绑定 LINE 账号"}
          </Button>
        </form>
        <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
          忘记密码或未收到账号？请联系系统管理员。
        </p>
      </CardContent>
    </Card>
  );
}

export default function BindPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-4 bg-background">
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[600px] h-[600px] bg-primary/3 rounded-full blur-[120px]" />
      </div>

      <div className="relative w-full max-w-sm space-y-8">
        <div className="text-center space-y-3">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-emerald-500/10">
            {/* Simple speech-bubble icon — calls out the messaging context */}
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              className="text-emerald-600"
              aria-hidden
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </div>
          <div>
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              LINE 账号绑定
            </h1>
            <p className="text-muted-foreground text-sm mt-1">
              CruiseAgent · 邮轮供应链助手
            </p>
          </div>
        </div>

        {/* useSearchParams requires a Suspense boundary in Next.js App Router. */}
        <Suspense fallback={<BindFormFallback />}>
          <BindForm />
        </Suspense>

        <p className="text-muted-foreground text-xs text-center">
          绑定链接为一次性、30 分钟内有效
        </p>
      </div>
    </div>
  );
}

function BindFormFallback() {
  return (
    <Card className="border-border/50 bg-card/50">
      <CardContent className="pt-6 pb-6 flex items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </CardContent>
    </Card>
  );
}

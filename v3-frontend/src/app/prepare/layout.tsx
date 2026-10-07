"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { login, getMe, logoutApi } from "@/lib/api";
import { clearAuth, getToken, saveAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function PreparationLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [checking, setChecking] = useState(true);
  const [email, setEmail] = useState("clean-admin@cruise.local");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const token = getToken();
    if (!token) { setChecking(false); return; }
    getMe(token).then(user => {
      if (!active) return;
      if (!["admin", "superadmin"].includes(user.role)) throw new Error("需要管理员账号");
      setReady(true);
    }).catch(() => { if (active) clearAuth(); }).finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, []);
  if (checking) return <div className="p-12 text-center text-sm">正在连接数据整理页面…</div>;
  if (!ready) return <main className="min-h-screen bg-muted/20 flex items-center justify-center p-6"><form className="w-full max-w-sm space-y-4 rounded-md border bg-background p-7" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError("");
    try { const result = await login({ email, password }); if (!["admin", "superadmin"].includes(result.user.role)) throw new Error("请使用管理员账号"); if (result.user.is_default_password) throw new Error("账号需先完成初始密码设置"); saveAuth(result.access_token, result.user, result.refresh_token); setReady(true); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "登录失败"); }
    finally { setBusy(false); }
  }}><h1 className="text-xl font-semibold">数据整理</h1><p className="text-sm text-muted-foreground">登录后配置数据表、上传 Excel 和查看产品价格记录。</p><label className="block text-sm">邮箱<Input type="email" autoComplete="username" className="mt-2" value={email} onChange={event => setEmail(event.target.value)} required /></label><label className="block text-sm">密码<Input type="password" autoComplete="current-password" className="mt-2" value={password} onChange={event => setPassword(event.target.value)} required /></label>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}<Button className="w-full" disabled={busy}>{busy ? "登录中…" : "进入数据整理"}</Button></form></main>;
  return <div className="min-h-screen bg-muted/20"><header className="border-b bg-background"><div className="mx-auto max-w-[1600px] flex items-center justify-between px-6 py-4"><Link href="/prepare" className="text-lg font-semibold">数据整理</Link><span className="text-xs text-muted-foreground">新数据库</span><Button variant="ghost" size="sm" onClick={async () => { try { await logoutApi(); clearAuth(); setReady(false); } catch { setError("退出失败，请重试"); } }}>退出</Button></div><nav className="mx-auto max-w-[1600px] flex gap-6 px-6 text-sm">{[["/prepare", "产品上传与数据"], ["/prepare/tables", "数据表与字段配置"]].map(([href, label]) => <Link key={href} href={href} className={`border-b-2 py-3 ${href === "/prepare" ? !pathname.startsWith("/prepare/tables") ? "border-primary font-medium" : "border-transparent" : pathname.startsWith(href) ? "border-primary font-medium" : "border-transparent"}`}>{label}</Link>)}</nav></header><main className="mx-auto max-w-[1600px]">{children}</main></div>;
}

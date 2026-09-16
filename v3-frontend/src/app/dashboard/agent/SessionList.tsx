"use client";

/**
 * Session list — left sidebar in /dashboard/agent/chat.
 *
 * Loads from GET /api/chat/sessions, renders most-recent-first. Click a
 * session → router.push to that session id; "新建对话" clears state.
 */

import { useEffect, useState } from "react";
import { Plus, MessageSquare, Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { listSessions, deleteSession, type V3Session } from "@/lib/v3-chat-api";
import { toast } from "sonner";

interface Props {
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: () => void;
}

export function SessionList({
  activeSessionId,
  onSelectSession,
  onNewSession,
}: Props) {
  const [sessions, setSessions] = useState<V3Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function refresh() {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch {
      // silent — sidebar can be empty if backend hiccups
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  // Refresh when active session changes (so freshly-created session
  // shows up + any title updates land).
  useEffect(() => {
    refresh();
  }, [activeSessionId]);

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm("删除会话？")) return;
    setDeletingId(id);
    try {
      await deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (activeSessionId === id) onNewSession();
      toast.success("已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="w-64 shrink-0 border-r flex flex-col h-full bg-background">
      <div className="p-3 border-b">
        <Button
          onClick={onNewSession}
          variant="outline"
          className="w-full justify-start gap-2 h-9"
          size="sm"
        >
          <Plus className="h-3.5 w-3.5" />
          新建对话
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {loading && (
          <div className="text-xs text-muted-foreground p-3 text-center">
            加载中…
          </div>
        )}
        {!loading && sessions.length === 0 && (
          <div className="text-xs text-muted-foreground p-3 text-center">
            还没有对话
          </div>
        )}
        {sessions.map((s) => {
          const isActive = s.id === activeSessionId;
          return (
            // Outer is a div with role=button + keyboard handlers because
            // we need a real <button> for the inner delete action and HTML
            // doesn't allow nested buttons (causes a hydration error).
            <div
              key={s.id}
              role="button"
              tabIndex={0}
              onClick={() => onSelectSession(s.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelectSession(s.id);
                }
              }}
              className={cn(
                "group w-full flex items-start gap-2 rounded-md px-2.5 py-2 text-left transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50 text-foreground/80"
              )}
            >
              <MessageSquare className="h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium truncate">{s.title}</p>
                {s.updated_at && (
                  <p className="text-[10px] text-muted-foreground">
                    {new Date(s.updated_at).toLocaleString("zh-CN", {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </p>
                )}
              </div>
              <button
                onClick={(e) => handleDelete(e, s.id)}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive shrink-0 p-0.5"
                aria-label="删除"
              >
                {deletingId === s.id ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Trash2 className="h-3 w-3" />
                )}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

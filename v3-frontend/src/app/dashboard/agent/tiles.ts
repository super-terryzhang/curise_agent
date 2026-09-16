/**
 * Quick-start tiles for the empty-state grid on /dashboard/agent.
 *
 * Adding a new tile: append one entry. Tile id must be unique (the
 * test pins this). `prompt: ""` means "free dialogue" — clicking should
 * just focus the composer, not send anything.
 */

import type { ComponentType } from "react";
import {
  Upload,
  Search,
  ClipboardList,
  Mail,
  FileSearch,
  BarChart3,
  MessageCircle,
} from "lucide-react";

export interface Tile {
  id: string;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  /**
   * The message to send when this tile is clicked. Empty string means
   * "just focus the composer, send nothing" — used for free-dialogue tile.
   */
  prompt: string;
}

export const TILES: Tile[] = [
  {
    id: "upload",
    label: "产品数据上传",
    description: "打开四步 Excel 上传模块",
    icon: Upload,
    prompt: "",
  },
  {
    id: "query",
    label: "查询订单",
    description: "按船 / 状态筛选",
    icon: Search,
    prompt: "帮我列出最近的订单",
  },
  {
    id: "order-detail",
    label: "订单详情",
    description: "看 PO + 匹配结果",
    icon: ClipboardList,
    prompt: "给我看订单 #",
  },
  {
    id: "inquiry",
    label: "询价生成",
    description: "为订单生成询价单",
    icon: Mail,
    prompt: "为订单 # 生成询价单",
  },
  {
    id: "doc",
    label: "文档查看",
    description: "翻看上传过的文档",
    icon: FileSearch,
    prompt: "我上传过哪些文档？",
  },
  {
    id: "analyze",
    label: "数据分析",
    description: "聚合统计 / 异常排查",
    icon: BarChart3,
    prompt: "本月匹配率最低的 5 个订单是哪些？",
  },
  {
    id: "free",
    label: "自由对话",
    description: "想问什么问什么",
    icon: MessageCircle,
    prompt: "",
  },
];

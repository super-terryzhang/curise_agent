import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SetupProduct } from "@/lib/database-setup-api";
import { PREPARATION_PATH } from "@/lib/data-preparation-routes";

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

export function ProductTable({ products }: { products: SetupProduct[] }) {
  return (
    <div className="overflow-hidden rounded-md border bg-background">
      <Table>
        <TableHeader className="bg-muted/40">
          <TableRow>
            <TableHead>产品代码</TableHead><TableHead>港口</TableHead>
            <TableHead>产品名称</TableHead><TableHead>供应商</TableHead>
            <TableHead>单位</TableHead><TableHead>商品分类</TableHead>
            <TableHead>品牌</TableHead><TableHead>业务分类</TableHead>
            <TableHead>状态</TableHead><TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {products.map((product) => (
            <TableRow key={product.id}>
              <TableCell className="font-mono text-xs">{display(product.code)}</TableCell>
              <TableCell>{display(product.port)}</TableCell>
              <TableCell className="max-w-72 whitespace-normal font-medium">{product.name}</TableCell>
              <TableCell>{display(product.supplier)}</TableCell>
              <TableCell>{display(product.unit)}</TableCell>
              <TableCell>{display(product.category)}</TableCell>
              <TableCell>{display(product.brand)}</TableCell>
              <TableCell>{display(product.extensions.find((item) => item.label === "业务分类")?.value)}</TableCell>
              <TableCell>{product.status ? "启用" : "停用"}</TableCell>
              <TableCell className="text-right">
                <Link className="text-xs font-medium text-primary hover:underline" href={`${PREPARATION_PATH}/products/${product.id}`}>查看</Link>
              </TableCell>
            </TableRow>
          ))}
          {products.length === 0 && (
            <TableRow><TableCell colSpan={10} className="h-24 text-center text-muted-foreground">暂无产品</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

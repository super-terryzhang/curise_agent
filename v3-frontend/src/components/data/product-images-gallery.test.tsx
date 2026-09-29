import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ProductImage } from "@/lib/data-api";
import { ProductImageGrid, ProductImagesPanel } from "./product-images-gallery";

const images: ProductImage[] = [
  {
    id: 1,
    filename: "apple-main.jpg",
    file_type: "image/jpeg",
    file_size_bytes: 1024,
    display_order: 0,
    alt_text: null,
    uploaded_at: "2026-09-29T00:00:00Z",
    thumbnail_url: "/thumb-1.jpg",
    medium_url: "/medium-1.jpg",
    full_url: "/full-1.jpg",
  },
  {
    id: 2,
    filename: "apple-side.webp",
    file_type: "image/webp",
    file_size_bytes: 2048,
    display_order: 1,
    alt_text: "侧面",
    uploaded_at: "2026-09-29T00:00:00Z",
    thumbnail_url: "/thumb-2.webp",
    medium_url: "/medium-2.webp",
    full_url: "/full-2.webp",
  },
];

describe("ProductImageGrid", () => {
  it("shows the exact image count, primary badge and action labels", () => {
    const html = renderToStaticMarkup(
      <ProductImageGrid images={images} readOnly={false} onView={vi.fn()} onDelete={vi.fn()} />,
    );

    expect(html).toContain("共 2 张");
    expect(html).toContain("主图");
    expect(html).toContain("查看大图");
    expect(html).toContain("删除");
  });

  it("hides delete controls for read-only users and explains the empty state", () => {
    const readOnlyHtml = renderToStaticMarkup(
      <ProductImageGrid images={images} readOnly onView={vi.fn()} onDelete={vi.fn()} />,
    );
    const emptyHtml = renderToStaticMarkup(
      <ProductImageGrid images={[]} readOnly onView={vi.fn()} onDelete={vi.fn()} />,
    );

    expect(readOnlyHtml).toContain("查看大图");
    expect(readOnlyHtml).not.toContain("删除");
    expect(emptyHtml).toContain("还没有图片，等待有权限的用户上传");
  });
});

describe("ProductImagesPanel", () => {
  it("uses the currently supported upload types in an embedded panel", () => {
    const html = renderToStaticMarkup(
      <ProductImagesPanel productId={42} productName="APPLE RED" readOnly={false} />,
    );

    expect(html).toContain("产品图片");
    expect(html).toContain("APPLE RED");
    expect(html).toContain("JPG / PNG / WebP");
    expect(html).toContain('accept="image/jpeg,image/png,image/webp"');
    expect(html).toContain("点击选择文件");
    expect(html).not.toContain("role=\"dialog\"");
  });

  it("does not render upload controls in read-only mode", () => {
    const html = renderToStaticMarkup(
      <ProductImagesPanel productId={42} productName="APPLE RED" readOnly />,
    );

    expect(html).not.toContain("点击选择文件");
    expect(html).not.toContain("type=\"file\"");
  });
});

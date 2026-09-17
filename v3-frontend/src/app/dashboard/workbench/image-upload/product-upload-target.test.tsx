import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ProductImage, ProductItem } from "@/lib/data-api";
import {
  CompactImageDropzone,
  SelectedProductImagePreview,
} from "./product-upload-target";

const product = {
  id: 71,
  code: "99PRD80671",
  product_name_en: "TEA ICED VITALITY 32 OZ",
  product_name_jp: null,
  port_name: "大阪",
  image_count: 2,
} as ProductItem;

const images = [
  {
    id: 10,
    filename: "tea-main.jpg",
    display_order: 0,
    thumbnail_url: "https://example.test/main.jpg",
  },
  {
    id: 11,
    filename: "tea-side.jpg",
    display_order: 1,
    thumbnail_url: "https://example.test/side.jpg",
  },
] as ProductImage[];

describe("image upload target", () => {
  it("shows all current images and identifies the primary image for the selected product", () => {
    const html = renderToStaticMarkup(
      <SelectedProductImagePreview
        product={product}
        images={images}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />,
    );

    expect(html).toContain("99PRD80671");
    expect(html).toContain("TEA ICED VITALITY 32 OZ");
    expect(html).toContain("大阪");
    expect(html).toContain("现有 2 张");
    expect(html).toContain("tea-main.jpg");
    expect(html).toContain("tea-side.jpg");
    expect(html).toContain("主图");
  });

  it("explains why no existing gallery is shown in automatic matching mode", () => {
    const html = renderToStaticMarkup(
      <SelectedProductImagePreview
        product={null}
        images={[]}
        loading={false}
        error={null}
        onRetry={vi.fn()}
      />,
    );

    expect(html).toContain("按文件名自动匹配");
    expect(html).toContain("完成匹配后展示产品图库");
  });

  it("uses a compact dropzone instead of the previous tall upload area", () => {
    const html = renderToStaticMarkup(
      <CompactImageDropzone
        busy={false}
        dragging={false}
        progress={{ done: 0, total: 0 }}
        onDraggingChange={vi.fn()}
        onFiles={vi.fn()}
      />,
    );

    expect(html).toContain("min-h-28");
    expect(html).not.toContain("min-h-56");
    expect(html).toContain("拖入图片或点击选择");
  });
});

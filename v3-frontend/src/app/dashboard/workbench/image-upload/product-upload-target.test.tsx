import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { BulkImageStagingRow } from "@/lib/bulk-images-api";
import type { ImageUploadProductOption, ProductImage } from "@/lib/data-api";
import {
  CompactImageDropzone,
  ProductImageSelectionTable,
} from "./product-upload-target";

const product = {
  id: 71,
  code: "99PRD80671",
  product_name_en: "TEA ICED VITALITY 32 OZ",
  product_name_jp: null,
  port_name: "大阪",
  image_count: 2,
} as ImageUploadProductOption;

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
  it("uses a wide business table and expands the selected row with current and staged images", () => {
    const html = renderToStaticMarkup(
      <ProductImageSelectionTable
        products={[product]}
        selectedProductId={product.id}
        selectedImages={images}
        selectedImagesLoading={false}
        selectedImagesError={null}
        stagedRows={[
          {
            id: 91,
            product_id: product.id,
            image_filename: "tea-new.jpg",
            preview_url: "https://example.test/new.jpg",
          } as BulkImageStagingRow,
        ]}
        busy={false}
        loading={false}
        onSelectProduct={vi.fn()}
        onRetryImages={vi.fn()}
        onFiles={vi.fn()}
      />,
    );

    expect(html).toContain("当前图片");
    expect(html).toContain("产品代码");
    expect(html).toContain("产品名称");
    expect(html).toContain("国家 / 港口");
    expect(html).toContain("本次新增");
    expect(html).toContain("99PRD80671");
    expect(html).toContain("TEA ICED VITALITY 32 OZ");
    expect(html).toContain("大阪");
    expect(html).toContain("现有 2 张");
    expect(html).toContain("现有产品图片（2）");
    expect(html).toContain("tea-main.jpg");
    expect(html).toContain("tea-side.jpg");
    expect(html).toContain("tea-new.jpg");
    expect(html).toContain("主图");
    expect(html).toContain("为此产品选择图片");
    expect(html).not.toContain("供应商");
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
    expect(html).not.toContain("文件夹");
  });
});

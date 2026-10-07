// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DeleteDialog, PriceEditDialog, ProductEditDialog } from "./edit-dialogs";

afterEach(cleanup);
describe("single product maintenance dialogs", () => {
  it("submits a changed price with both dates and currency", () => {
    const save = vi.fn();
    render(<PriceEditDialog type="purchase" period={{ id: 1, product_id: 2, price_type: "purchase", amount: 100, currency: "JPY", effective_from: "2027-01-01", effective_to: "2027-03-31", status: true }} busy={false} error={null} onClose={vi.fn()} onSave={save} />);
    fireEvent.change(screen.getByLabelText("价格"), { target: { value: "135" } });
    fireEvent.click(screen.getByRole("button", { name: "保存区间" }));
    expect(save).toHaveBeenCalledWith({ amount: 135, currency: "JPY", effective_from: "2027-01-01", effective_to: "2027-03-31" });
  });
  it("requires the exact product code before permanent deletion", () => {
    const remove = vi.fn();
    render(<DeleteDialog preview={{ code: "TEST-A", expected_revision: 1, expected_extension_revision: 0, can_delete: true, reasons: [], period_count: 4 }} busy={false} error={null} onClose={vi.fn()} onDelete={remove} />);
    const button = screen.getByRole("button", { name: "确认永久删除" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("确认产品代码"), { target: { value: "TEST-A" } });
    fireEvent.click(button);
    expect(remove).toHaveBeenCalledWith("TEST-A");
    expect(screen.getByText(/全部 4 条价格区间/)).toBeTruthy();
  });
  it("shows configured numeric fields and allows an explicit empty optional value", () => {
    const save = vi.fn();
    render(<ProductEditDialog config={{ fields: [{ key: "unit", label: "单位", type: "text", required: false, options: [] }, { key: "extension:n", label: "测试数字", type: "number", required: true, options: [] }], values: { unit: "KG", "extension:n": "12" }, expected_revision: 1, extension_revision: 1, schema_version: 4 }} busy={false} error={null} onClose={vi.fn()} onSave={save} />);
    fireEvent.change(screen.getByLabelText("单位"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("测试数字"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(save).toHaveBeenCalledWith({ unit: "", "extension:n": "20" });
  });
});

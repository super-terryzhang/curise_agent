import { beforeEach, expect, it, vi } from "vitest";
import * as api from "./data-tables-api";
import { fetchWithAuth } from "./fetch-with-auth";
vi.mock("./fetch-with-auth", () => ({ fetchWithAuth: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
it("preserves record UUID, revision and field issues at the HTTP boundary", async () => {
  vi.mocked(fetchWithAuth).mockResolvedValue(new Response(JSON.stringify({detail: {
    code: "STALE_REVISION", message: "请刷新", issues: [{field_id:"f", code:"REQUIRED", message:"必填"}]
  }}), {status:409}));
  const error = await api.updateRecord("t", "r", {expected_revision:2, schema_version:3, values:{f:null}}).catch(e=>e);
  expect(error).toMatchObject({status:409, code:"STALE_REVISION", issues:[{field_id:"f", message:"必填"}]});
  const [url, init] = vi.mocked(fetchWithAuth).mock.calls[0];
  expect(url).toContain("/api/data-tables/t/records/r");
  expect(JSON.parse(String(init?.body))).toEqual({expected_revision:2, schema_version:3, values:{f:null}});
});
it("explicit retry keeps the same create UUID after uncertain network result", async () => {
  vi.mocked(fetchWithAuth).mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(new Response(JSON.stringify({id:"same"})));
  const body = {id:"same", schema_version:2, values:{f:"0"}};
  const error = await api.createRecord("t", body).catch(e=>e);
  expect(error.uncertain).toBe(true);
  expect(error.message).toContain("无法确认");
  await api.createRecord("t", body);
  expect(vi.mocked(fetchWithAuth).mock.calls.map(c=>JSON.parse(String(c[1]?.body)).id)).toEqual(["same","same"]);
});
it("encodes typed filters and target search without injecting URL parameters", async () => {
  vi.mocked(fetchWithAuth).mockResolvedValue(new Response(JSON.stringify({items:[]})));
  await api.listRecords("t", {filters:[{field_id:"f", operator:"eq", value:"a&status=archived"}]});
  const url = new URL(String(vi.mocked(fetchWithAuth).mock.calls[0][0]));
  expect(JSON.parse(url.searchParams.get("filters")!)[0].value).toBe("a&status=archived");
  expect(url.searchParams.get("status")).toBeNull();
});

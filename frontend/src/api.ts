import type {
  BacktestRequest,
  BacktestResponse,
  CapabilityResponse,
  DurableStatus,
  WorkflowReceipt,
  WorkflowRequest,
  WorkflowResult
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly requestId?: string
  ) {
    super(message);
  }
}

async function readJson(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}

function errorMessage(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const payload = await readJson(response);
  if (!response.ok) {
    const requestId =
      payload && typeof payload === "object" && "request_id" in payload
        ? String(payload.request_id)
        : undefined;
    throw new ApiError(
      errorMessage(payload, "请求失败（HTTP " + response.status + "）"),
      response.status,
      requestId
    );
  }
  return payload as T;
}

async function workflowRequest(path: string, options?: RequestInit): Promise<WorkflowResult> {
  const response = await fetch(path, options);
  const payload = await readJson(response);
  if (
    payload &&
    typeof payload === "object" &&
    "request_id" in payload &&
    "status" in payload &&
    [200, 202, 409, 502, 503].includes(response.status)
  ) {
    return payload as WorkflowResult;
  }
  if (!response.ok) {
    throw new ApiError(
      errorMessage(payload, "任务请求失败（HTTP " + response.status + "）"),
      response.status
    );
  }
  throw new ApiError("服务端返回的任务结果不可识别", response.status);
}

export const api = {
  capabilities: () => request<CapabilityResponse>("/v1/capabilities"),
  backtest: (payload: BacktestRequest) =>
    request<BacktestResponse>("/v1/evaluations/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  run: (payload: WorkflowRequest, durable: boolean) =>
    workflowRequest("/v1/workflows/run?durable=" + String(durable), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  status: (requestId: string) =>
    request<DurableStatus>("/v1/workflows/" + encodeURIComponent(requestId)),
  receipt: (requestId: string) =>
    request<WorkflowReceipt>("/v1/workflows/receipts/" + encodeURIComponent(requestId)),
  resume: (requestId: string) =>
    workflowRequest("/v1/workflows/" + encodeURIComponent(requestId) + "/resume", {
      method: "POST"
    })
};

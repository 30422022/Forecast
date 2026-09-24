export interface CapabilityResponse {
  mode: "public_preview";
  private_assets_loaded: false;
  forecast_provider: string;
  decision_provider: string;
  storage: "memory" | "postgresql_aes_gcm" | "unavailable";
  recovery_available: boolean;
  backtest_available: boolean;
  limitations: string[];
}

export interface BacktestRequest {
  history: number[];
  horizon: number;
  max_folds: number;
}

export interface BacktestFold {
  cutoff_index: number;
  train_rows: number;
  test_rows: number;
  mae: number;
  rmse: number;
  wape: number | null;
  smape: number;
}

export interface BacktestResponse {
  demo_only: true;
  method: "rolling_origin_expanding_window";
  provider: string;
  horizon: number;
  fold_count: number;
  completed_folds: number;
  folds: BacktestFold[];
  failures: { cutoff_index: number; reason_code: string }[];
  mean_mae: number | null;
  mean_rmse: number | null;
  mean_wape: number | null;
  mean_smape: number | null;
}

export interface TraceStep {
  node: "quality" | "forecast" | "review" | "decision" | "audit";
  status: "succeeded" | "skipped" | "failed";
  detail: string;
}

export interface WorkflowRequest {
  site_id: string;
  history: number[];
  horizon: number;
}

export interface WorkflowSuccess {
  request_id: string;
  status: "demo_only";
  forecast: number[];
  decision: {
    status: string;
    message?: string;
    actionable?: boolean;
  };
  trace: TraceStep[];
  provenance: {
    demo_only: boolean;
    fingerprint: string;
    fingerprint_scope: string;
    private_assets_loaded: boolean;
  };
}

export interface WorkflowFailure {
  request_id: string;
  status: "failed";
  error_code: string;
  failed_node: string;
  trace: TraceStep[];
  provenance: WorkflowSuccess["provenance"];
}

export interface WorkflowPending {
  request_id: string;
  status: "queued" | "running" | "recoverable";
  error_code?: string | null;
}

export type WorkflowResult = WorkflowSuccess | WorkflowFailure | WorkflowPending;

export interface AgentMessage {
  message_id: string;
  request_id: string;
  sender: string;
  recipient: string;
  kind: string;
  attempt: number;
  verdict: string | null;
  reason_code: string;
  created_at: string;
}

export interface DurableStatus {
  request_id: string;
  status: "queued" | "running" | "recoverable" | "succeeded" | "failed";
  next_stage: string | null;
  attempt: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  messages: AgentMessage[];
}

export interface WorkflowReceipt {
  request_id: string;
  status: DurableStatus["status"];
  last_node: string;
  error_code: string | null;
  created_at: string;
  expires_at: string | null;
}

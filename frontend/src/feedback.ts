export type NoticeKind = "success" | "warning" | "error" | "info";

export interface NoticeRequest {
  title: string;
  detail?: string;
  kind?: NoticeKind;
}

export interface ConfirmRequest {
  eyebrow?: string;
  title: string;
  copy: string;
  consequences: Array<{ tone?: NoticeKind; title: string; detail: string }>;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
}

export function notify(request: NoticeRequest) {
  window.dispatchEvent(new CustomEvent("hive:notice", { detail: request }));
}

export function confirmAction(request: ConfirmRequest): Promise<boolean> {
  return new Promise((resolve) => {
    window.dispatchEvent(new CustomEvent("hive:confirm", { detail: { ...request, resolve } }));
  });
}

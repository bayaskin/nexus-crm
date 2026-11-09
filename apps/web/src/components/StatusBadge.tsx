
import type { OrderStatus } from "../types";

const labels: Record<OrderStatus, string> = {
  draft: "Draft",
  confirmed: "Confirmed",
  shipped: "Shipped",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

export default function StatusBadge({
  status,
}: {
  status: OrderStatus;
}) {
  return (
    <span className={`status-badge status-${status}`}>
      <span className="status-dot" />
      {labels[status] ?? status}
    </span>
  );
}

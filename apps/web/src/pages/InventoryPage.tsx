import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Clock3, Plus, Search, X } from "lucide-react";
import { ApiError } from "../api/client";
import {
  createInventoryAdjustment,
  getInventoryMovements,
  listInventory,
  updateInventoryReorderLevel,
} from "../api/inventory";
import { useAuth } from "../auth/AuthContext";
import type { InventoryBalance, InventoryMovement, InventoryReason } from "../types";

const PAGE_SIZE = 20;
const reasons: InventoryReason[] = ["purchase_receipt", "customer_return", "damaged", "lost", "correction", "initial_stock", "other"];
const reasonLabels: Record<InventoryReason, string> = {
  purchase_receipt: "Purchase receipt", customer_return: "Customer return", damaged: "Damaged", lost: "Lost", correction: "Correction", initial_stock: "Initial stock", other: "Other",
};
const dateTime = (value: string) => new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));

export default function InventoryPage() {
  const { can } = useAuth();
  const canAdjust = can("inventory:adjust");
  const [items, setItems] = useState<InventoryBalance[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [search, setSearch] = useState("");
  const [lowStock, setLowStock] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [adjusting, setAdjusting] = useState<InventoryBalance | "open" | null>(null);
  const [products, setProducts] = useState<InventoryBalance[]>([]);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [productId, setProductId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState<InventoryReason>("purchase_receipt");
  const [referenceId, setReferenceId] = useState("");
  const [notes, setNotes] = useState("");
  const [reorder, setReorder] = useState("");
  const [history, setHistory] = useState<InventoryBalance | null>(null);
  const [movements, setMovements] = useState<InventoryMovement[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [reorderEditId, setReorderEditId] = useState("");
  const [reorderValue, setReorderValue] = useState("");
  const [reorderError, setReorderError] = useState("");
  const loadSequence = useRef(0);

  const loadInventory = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true); setError("");
    try {
      const result = await listInventory({ search, low_stock_only: lowStock, skip, limit: PAGE_SIZE });
      if (sequence !== loadSequence.current) return;
      setItems(result.items); setTotal(result.total);
    } catch (err) {
      if (sequence !== loadSequence.current) return;
      setError(err instanceof ApiError ? err.message : "Unable to load inventory.");
    } finally { if (sequence === loadSequence.current) setLoading(false); }
  }, [search, lowStock, skip]);

  useEffect(() => { void loadInventory(); }, [loadInventory]);

  async function openAdjustment(row?: InventoryBalance) {
    setFormError(""); setQuantity(""); setReason("purchase_receipt"); setReferenceId(""); setNotes(""); setReorder(""); setProductId(row?.product_id ?? ""); setAdjusting(row ?? "open");
    try {
      const firstPage = await listInventory({ skip: 0, limit: 100 });
      let allProducts = firstPage.items;
      for (let offset = firstPage.limit; offset < firstPage.total; offset += firstPage.limit) {
        const nextPage = await listInventory({ skip: offset, limit: firstPage.limit });
        allProducts = allProducts.concat(nextPage.items);
      }
      setProducts(allProducts);
    }
    catch (err) { setFormError(err instanceof ApiError ? err.message : "Unable to load products."); }
  }

  async function submitAdjustment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const change = Number(quantity);
    if (!Number.isInteger(change) || change === 0) { setFormError("Enter a non-zero whole number."); return; }
    setSaving(true); setFormError("");
    try {
      const result = await createInventoryAdjustment({ product_id: productId, quantity_change: change, reason, reference_id: referenceId.trim() || undefined, notes: notes.trim() || undefined, ...(reorder === "" ? {} : { reorder_level: Number(reorder) }) });
      setItems((current) => current.map((item) => item.product_id === result.balance.product_id ? result.balance : item));
      setAdjusting(null);
      await loadInventory();
    } catch (err) {
      setFormError(err instanceof ApiError ? (err.status === 409 ? "Insufficient stock: this removal would make the balance negative." : err.message) : "Unable to adjust inventory.");
    } finally { setSaving(false); }
  }

  async function saveReorderLevel(row: InventoryBalance) {
    const value = Number(reorderValue);
    if (!Number.isInteger(value) || value < 0) { setReorderError("Enter a whole number greater than or equal to zero."); return; }
    setReorderError("");
    try {
      const updated = await updateInventoryReorderLevel(row.product_id, value);
      setItems((current) => current.map((item) => item.product_id === updated.product_id ? updated : item));
      setReorderEditId("");
    } catch (err) { setReorderError(err instanceof ApiError ? err.message : "Unable to update reorder level."); }
  }

  async function openHistory(row: InventoryBalance) {
    setHistory(row); setMovements([]); setHistoryError(""); setHistoryLoading(true);
    try { setMovements(await getInventoryMovements(row.product_id)); }
    catch (err) { setHistoryError(err instanceof ApiError ? err.message : "Unable to load movement history."); }
    finally { setHistoryLoading(false); }
  }

  const page = Math.floor(skip / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <div>
    <div className="page-heading"><div><div className="eyebrow">OPERATIONS / STOCK</div><h1>Inventory</h1><p className="muted">Track stock levels and review inventory movements.</p></div>
      {canAdjust && <button className="primary-button" onClick={() => void openAdjustment()}><Plus size={17} />Adjust stock</button>}
    </div>
    <section className="panel inventory-panel">
      <div className="table-toolbar">
        <label className="search-field"><Search size={17} /><input aria-label="Search inventory" placeholder="Search product name or SKU..." value={search} onChange={(event) => { setSearch(event.target.value); setSkip(0); }} /></label>
        <label className="inventory-filter"><input type="checkbox" checked={lowStock} onChange={(event) => { setLowStock(event.target.checked); setSkip(0); }} /> Low stock only</label>
      </div>
      {error && <div className="alert alert-error" role="alert">{error}</div>}
      <div className="table-wrapper"><table className="data-table"><thead><tr><th>PRODUCT</th><th>WAREHOUSE</th><th>ON HAND</th><th>REORDER LEVEL</th><th>STATUS</th><th>LAST UPDATED</th><th /></tr></thead><tbody>
        {!loading && items.map((row) => <tr key={`${row.product_id}-${row.warehouse_id}`}>
          <td><strong>{row.product_name}</strong><div className="inventory-sku">{row.sku}</div></td><td>{row.warehouse_code}</td><td><strong>{row.quantity_on_hand}</strong></td>
          <td>{canAdjust && reorderEditId === row.product_id ? <div className="reorder-edit"><input aria-label={`Reorder level for ${row.product_name}`} type="number" min="0" step="1" value={reorderValue} onChange={(event) => setReorderValue(event.target.value)} /><button className="text-link" onClick={() => void saveReorderLevel(row)}>Save</button><button className="text-link" onClick={() => setReorderEditId("")}>Cancel</button></div> : <>{row.reorder_level}{canAdjust && <button className="text-link reorder-link" onClick={() => { setReorderEditId(row.product_id); setReorderValue(String(row.reorder_level)); setReorderError(""); }}>Edit</button>}</>}</td>
          <td><span className={`inventory-stock-badge ${row.is_low_stock ? "is-low" : "is-healthy"}`}>{row.is_low_stock ? "Low stock" : "In stock"}</span></td><td>{dateTime(row.updated_at)}</td>
          <td><div className="inventory-actions"><button className="text-link" onClick={() => void openHistory(row)}>History</button>{canAdjust && <button className="text-link" onClick={() => void openAdjustment(row)}>Adjust</button>}</div></td>
        </tr>)}
        {!loading && items.length === 0 && <tr><td colSpan={7} className="empty-state">{error ? "Inventory could not be loaded." : "No inventory found. Try changing your search or filters."}</td></tr>}
        {loading && <tr><td colSpan={7} className="empty-state">Loading inventory...</td></tr>}
      </tbody></table></div>
      {reorderError && <div className="alert alert-error" role="alert">{reorderError}</div>}
      <div className="table-footer"><span>Showing {total === 0 ? 0 : skip + 1}–{Math.min(skip + PAGE_SIZE, total)} of {total}</span><div className="pagination"><button className="icon-button" disabled={skip === 0 || loading} onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))} aria-label="Previous page"><ChevronLeft size={18} /></button><span>{page} / {pages}</span><button className="icon-button" disabled={skip + PAGE_SIZE >= total || loading} onClick={() => setSkip(skip + PAGE_SIZE)} aria-label="Next page"><ChevronRight size={18} /></button></div></div>
    </section>

    {adjusting !== null && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setAdjusting(null); }}><section className="customer-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-adjust-title"><div className="customer-modal-heading"><div><div className="eyebrow">STOCK MANAGEMENT</div><h2 id="inventory-adjust-title">Adjust stock</h2></div><button className="icon-button" onClick={() => setAdjusting(null)} aria-label="Close dialog"><X size={18} /></button></div><form onSubmit={submitAdjustment}>
      <label>Product<select required aria-label="Product" value={productId} onChange={(event) => setProductId(event.target.value)}><option value="">Select product</option>{typeof adjusting !== "string" && adjusting && !products.some((product) => product.product_id === adjusting.product_id) && <option value={adjusting.product_id}>{adjusting.product_name} ({adjusting.sku})</option>}{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name} ({product.sku})</option>)}</select></label>
      <label>Quantity change *<input required type="number" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} aria-describedby="quantity-help" /><small id="quantity-help">Use a positive number to add stock or a negative number to remove it. Zero is invalid.</small></label>
      <label>Reason *<select required value={reason} onChange={(event) => setReason(event.target.value as InventoryReason)}>{reasons.map((value) => <option key={value} value={value}>{reasonLabels[value]}</option>)}</select></label>
      <label>Reference ID<input maxLength={80} value={referenceId} onChange={(event) => setReferenceId(event.target.value)} /></label>
      <label>Notes<textarea rows={3} maxLength={500} value={notes} onChange={(event) => setNotes(event.target.value)} /></label>
      <label>Update reorder level (optional)<input type="number" min="0" step="1" value={reorder} onChange={(event) => setReorder(event.target.value)} /></label>
      {formError && <div className="alert alert-error" role="alert">{formError}</div>}
      <div className="customer-modal-actions"><button type="button" className="secondary-button" onClick={() => setAdjusting(null)} disabled={saving}>Cancel</button><button type="submit" className="primary-button" disabled={saving || !productId || !quantity || Number(quantity) === 0}>{saving ? "Saving..." : "Save adjustment"}</button></div>
    </form></section></div>}

    {history && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setHistory(null); }}><section className="customer-modal inventory-history-modal" role="dialog" aria-modal="true" aria-labelledby="inventory-history-title"><div className="customer-modal-heading"><div><div className="eyebrow">INVENTORY ACTIVITY</div><h2 id="inventory-history-title">Movement history</h2><p className="muted">{history.product_name} · {history.sku}</p></div><button className="icon-button" onClick={() => setHistory(null)} aria-label="Close dialog"><X size={18} /></button></div>
      {historyLoading ? <div className="empty-state">Loading movement history...</div> : historyError ? <div className="alert alert-error" role="alert">{historyError}</div> : movements.length === 0 ? <div className="empty-state"><Clock3 size={21} /><strong>No movements yet.</strong></div> : <div className="movement-list">{movements.map((movement) => <article className="movement-entry" key={movement.id}><div className="movement-entry-head"><strong className={movement.quantity_change > 0 ? "movement-add" : "movement-remove"}>{movement.quantity_change > 0 ? `+${movement.quantity_change}` : movement.quantity_change} units</strong><time>{dateTime(movement.created_at)}</time></div><div className="movement-reason">{reasonLabels[movement.reason] ?? movement.reason}</div>{movement.reference_id && <div>Reference: {movement.reference_id}</div>}{movement.notes && <p>{movement.notes}</p>}</article>)}</div>}
    </section></div>}
  </div>;
}

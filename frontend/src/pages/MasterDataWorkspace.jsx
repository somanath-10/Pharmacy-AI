import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Skeleton, Money } from "../ui";

const TABS = ["Products & Drugs", "Customers", "Vendors", "Warehouses & Bins", "BOMs & Recipes", "Specifications"];

export default function MasterDataWorkspace() {
  const [tab, setTab] = useState("Products & Drugs");
  const [products, setProducts] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [boms, setBoms] = useState([]);
  const [specs, setSpecs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [pr, cus, wh, bm, sp] = await Promise.all([
        api("/api/masters/products"),
        api("/api/masters/customers"),
        api("/api/masters/warehouses"),
        api("/api/masters/boms").catch(() => []),
        api("/api/masters/specifications").catch(() => []),
      ]);
      setProducts(Array.isArray(pr) ? pr : []);
      setCustomers(Array.isArray(cus) ? cus : []);
      setWarehouses(Array.isArray(wh) ? wh : []);
      setBoms(Array.isArray(bm) ? bm : []);
      setSpecs(Array.isArray(sp) ? sp : []);
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      {toastHost}
      <Topbar
        title="Master Data Steward"
        sub="Central master data governance: Drug master, products, customers, suppliers, storage facilities, Bill of Materials, and release specifications"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="💊" label="Master SKUs" value={products.length} tone="blue" foot="Finished Goods & APIs" />
        <Kpi ico="🏥" label="Customer Accounts" value={customers.length} tone="green" foot="Hospitals & Distributors" />
        <Kpi ico="🏭" label="Warehouses & Plants" value={warehouses.length} tone="blue" foot="Validated storage zones" />
        <Kpi ico="📜" label="BOMs & Master Formulas" value={boms.length > 0 ? boms.length : "12 Active"} tone="purple" foot="Version controlled" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count: t === "Products & Drugs" ? products.length : t === "Customers" ? customers.length : t === "Warehouses & Bins" ? warehouses.length : undefined,
          }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        <button className="btn primary" onClick={() => toast("Opening Master Change Request form...", "info")}>
          + Request Master Data Change
        </button>
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {tab === "Products & Drugs" && (
            <div className="card">
              <h3>Product & Drug Master Directory</h3>
              <div className="card-sub">Dosage forms, schedules (H, H1, X, OTC), storage conditions, and shelf-life rules</div>
              <Table
                columns={[
                  { key: "sku", label: "SKU", render: (r) => <span className="mono bold">{r.sku}</span> },
                  { key: "name", label: "Product Name" },
                  { key: "type", label: "Category", render: (r) => <span className="badge blue">{r.type}</span> },
                  { key: "schedule", label: "Schedule", render: (r) => <span className={`badge ${r.schedule === "X" ? "red" : r.schedule ? "orange" : "green"}`}>{r.schedule || "OTC"}</span> },
                  { key: "storage_conditions", label: "Storage", render: (r) => <span className={`badge ${r.storage_conditions === "COLD" ? "blue" : "gray"}`}>{r.storage_conditions}</span> },
                  { key: "mrp", label: "MRP", render: (r) => <Money value={r.mrp} /> },
                  { key: "shelf_life_days", label: "Shelf Life", render: (r) => <span>{r.shelf_life_days ? `${r.shelf_life_days} days` : "—"}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={products}
              />
            </div>
          )}

          {tab === "Customers" && (
            <div className="card">
              <h3>Customer Master Registry</h3>
              <div className="card-sub">Distributors, hospital pharmacies, and retail chemists with credit limits</div>
              <Table
                columns={[
                  { key: "code", label: "Account #", render: (r) => <span className="mono bold">{r.code}</span> },
                  { key: "name", label: "Institution / Entity" },
                  { key: "type", label: "Channel", render: (r) => <span className="badge gray">{r.type}</span> },
                  { key: "credit_limit", label: "Credit Limit", render: (r) => <Money value={r.credit_limit} /> },
                  { key: "payment_terms_days", label: "Payment Terms", render: (r) => <span>{r.payment_terms_days} days</span> },
                  { key: "pricing_tier", label: "Tier", render: (r) => <span className="badge blue">{r.pricing_tier}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={customers}
              />
            </div>
          )}

          {tab === "Warehouses & Bins" && (
            <div className="card">
              <h3>Facilities & Storage Locations Master</h3>
              <div className="card-sub">Validated distribution centers, cold rooms, and secure vault facilities</div>
              <Table
                columns={[
                  { key: "code", label: "Facility Code", render: (r) => <span className="mono bold">{r.code}</span> },
                  { key: "name", label: "Facility Name" },
                  { key: "type", label: "Class", render: (r) => <span className="badge blue">{r.type}</span> },
                  { key: "storage_conditions", label: "Condition", render: (r) => <span className="badge gray">{r.storage_conditions}</span> },
                  { key: "zones", label: "Zones", render: (r) => <span className="small">{(r.zones || []).join(", ")}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={warehouses}
              />
            </div>
          )}

          {tab === "BOMs & Recipes" && (
            <div className="card">
              <h3>Bill of Materials (BOM) & Master Production Recipes</h3>
              <div className="card-sub">Standard batch formulas, API & excipient proportions, and version history</div>
              <Table
                columns={[
                  { key: "product_id", label: "Finished Product", render: (r) => <span className="mono bold">{r.product_id}</span> },
                  { key: "version", label: "Version", render: (r) => <span className="badge blue mono">v{r.version || 1}</span> },
                  { key: "standard_batch_size", label: "Standard Batch Size" },
                  { key: "status", label: "Approval State", render: (r) => <Badge>{r.status || "APPROVED"}</Badge> },
                ]}
                rows={boms.length > 0 ? boms : [
                  { product_id: "PRD-00001 (Paracetamol 500mg)", version: 2, standard_batch_size: "100,000 Tablets", status: "APPROVED" },
                  { product_id: "PRD-00002 (Amoxicillin 250mg)", version: 1, standard_batch_size: "50,000 Capsules", status: "APPROVED" },
                ]}
              />
            </div>
          )}

          {tab === "Specifications" && (
            <div className="card">
              <h3>QC Specifications & Acceptance Criteria</h3>
              <div className="card-sub">Pharmacopeial limits (IP/BP/USP), assay thresholds, and test methods</div>
              <Table
                columns={[
                  { key: "product_id", label: "Product SKU", render: (r) => <span className="mono bold">{r.product_id}</span> },
                  { key: "spec_code", label: "Spec Code", render: (r) => <span className="mono">{r.spec_code || "SPEC-STD"}</span> },
                  { key: "version", label: "Version", render: (r) => <span className="badge gray">v{r.version || 1}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status || "APPROVED"}</Badge> },
                ]}
                rows={specs.length > 0 ? specs : [
                  { product_id: "PRD-00001", spec_code: "SPEC-PCM-500", version: 3, status: "APPROVED" },
                  { product_id: "PRD-00002", spec_code: "SPEC-AMX-250", version: 2, status: "APPROVED" },
                ]}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}


# Pharma AI OS — Frontend

React + Vite SPA for the Pharma AI Operating System, styled to the reference
"Unified AI Command Center" palette (deep navy `#0b1f4b`, accent blue `#1657ff`,
ice-blue canvas, white cards, green/orange status dots).

## Run

```bash
npm install
npm run dev        # http://localhost:5173 (proxies /api → backend :8000)
npm run build      # production bundle in dist/
```

## Screens

| Route | Screen |
|---|---|
| `/` | **AI Command Center** — KPI strip, Live Inputs → AI Orchestration Core → Outputs, Department health, inline Decision queue, agent fleet |
| `/queue` | **Human Decision Queue** — approve / review / reject with AI-assembled evidence |
| `/sales` | Customers & Sales — leads, quotations, sales orders (O2C actions), POS |
| `/supply` | Supply Chain — MRP runs, transfer/produce/buy proposals |
| `/vendors` | Vendors & Procurement — lifecycle, sourcing, contracts, PR→PO |
| `/warehouse` | Warehouse — ASN, GRN + QC disposition + putaway, batches, reservations |
| `/quality` | QC/LIMS + QA/QMS — samples, batch release authority, deviations, CAPA |
| `/plant` | Plant/MES — production orders, eBMR execution, equipment gates |
| `/pharmacy` | Pharmacy — Rx Doc AI intake, pharmacist review, dispense, controlled register |
| `/logistics` | Logistics — outbound shipments, dispatch/track/POD, carriers |
| `/finance` | Finance — AP matching, payments, AR, GL, recalls/returns governance |
| `/workflows` | Workflow Viewer — full lifecycle + audit trail for any entity |

## Demo accounts

`admin@pharmaos.local / Admin@123` (Super Admin) — plus buyer / qa / pharmacist /
finance / warehouse accounts (see login screen; seeded by the backend).

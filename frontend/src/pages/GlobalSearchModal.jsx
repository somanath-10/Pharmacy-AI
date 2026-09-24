import React, { useEffect, useState, useRef } from "react";
import { api } from "../api";
import { useNavigate } from "react-router-dom";

export default function GlobalSearchModal({ open, onClose }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    if (!query || query.trim().length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await api(`/api/analytics/search?q=${encodeURIComponent(query.trim())}`);
        setResults(res.results || []);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  if (!open) return null;

  const handleSelect = (r) => {
    onClose();
    if (r.link) navigate(r.link);
  };

  return (
    <div className="search-modal-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="search-modal">
        <div className="search-input-wrap">
          <span style={{ fontSize: 18 }}>🔍</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
            placeholder="Search products, orders, batches, customers, vendors, recalls... (Esc to close)"
          />
          {loading && <span className="spin" style={{ width: 16, height: 16 }} />}
          <kbd style={{ background: "#e2e8f0", padding: "2px 6px", borderRadius: 4, fontSize: 11, color: "#64748b" }}>ESC</kbd>
        </div>

        <div className="search-results-list">
          {results.length > 0 ? (
            results.map((r, i) => (
              <div key={i} className="search-result-item" onClick={() => handleSelect(r)}>
                <div>
                  <div className="search-result-title">{r.title}</div>
                  <div className="search-result-sub">{r.subtitle}</div>
                </div>
                <span className="badge blue mono" style={{ fontSize: 11 }}>{r.category}</span>
              </div>
            ))
          ) : query.length >= 2 && !loading ? (
            <div style={{ padding: "30px 20px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
              No matches found across enterprise records for "{query}"
            </div>
          ) : (
            <div style={{ padding: "24px 20px", textAlign: "center", color: "var(--muted)", fontSize: 12.5 }}>
              Type at least 2 characters to search across products, lots, sales/purchase orders, and documents...
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


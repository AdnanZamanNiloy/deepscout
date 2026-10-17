import { useEffect, useState } from "react";
import {
  IconAgents, IconCompass, IconMissions, IconMore, IconPencil, IconPin,
  IconPlus, IconTrash,
} from "./icons";
import BrandLockup from "./Brand";

/* Primary navigation + the session list.
 *
 * The list is deliberately FLAT: a status dot and the title, one compact
 * single-line row per research session. An earlier version stacked the mode and
 * "7 min ago" under every title, which made the rail harder to scan than the
 * chat it points at — the sub-line competed with the titles and rows were twice
 * as tall for information that belongs on the session itself.
 *
 * Session management (rename / pin / delete) lives on each row's own "…" menu.
 * It used to live ONLY on the session header, which meant a session could not
 * be renamed, pinned or deleted from the rail at all — and a list you can read
 * but not act on is a worse index than one that can. The same three actions,
 * same handlers; the row is the anchor. The header keeps its own menu because a
 * pinned row is not always the open session.
 *
 * The row and the "…" are SIBLING buttons, never nested: the row must stay one
 * button so its hit target and its accessible name are the title, and a nested
 * control inside a button is invalid HTML and unreachable by keyboard.
 */
const NAV = [
  { id: "missions", label: "Research", icon: IconMissions },
  { id: "model-controls", label: "Model Controls", icon: IconCompass },
  { id: "agents", label: "Agents", icon: IconAgents },
];

export const STATUS_DOT = {
  running: "live",
  resumable: "warn",
  completed: "done",
  failed: "bad",
  aborted: "idle",
  cancelled: "idle",
};

export default function Sidebar({
  view, onNavigate, missions, activeSessionId, onOpenMission, onNew,
  open, onClose, onRename, onTogglePin, onDelete,
}) {
  const [menuSessionId, setMenuSessionId] = useState(null);
  const [editing, setEditing] = useState(null);

  /* One menu at a time, and it closes on the two things a menu must always
   * close on: a click anywhere else, and Escape. Scoped to the open session's
   * row so clicking inside the menu (or the row being renamed) does not close
   * it under the pointer. */
  useEffect(() => {
    if (menuSessionId == null) return;
    const onDown = (e) => {
      if (!e.target.closest(".session-row-wrap")) setMenuSessionId(null);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setMenuSessionId(null);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuSessionId]);

  const go = (id) => { onNavigate(id); onClose?.(); };

  const renderRow = (m) => {
    const label = m.title || m.query;
    const isActive = m.sessionId === activeSessionId;
    const isEditing = editing === m.sessionId;
    const menuOpen = menuSessionId === m.sessionId;
    return (
      <li key={m.sessionId} className={`session-row-wrap${menuOpen ? " open" : ""}`}>
        <button
          type="button"
          className={`session-row${isActive ? " active" : ""}`}
          aria-current={isActive ? "true" : undefined}
          onClick={() => { onOpenMission(m.sessionId); onClose?.(); }}
          title={label}
        >
          <span className={`dot ${STATUS_DOT[m.status] || "idle"}`} />
          {isEditing ? (
            <input
              className="session-rename-input"
              defaultValue={label}
              autoFocus
              aria-label={`Rename ${label}`}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") {
                  const clean = e.currentTarget.value.trim();
                  if (clean && clean !== label) onRename?.(m.sessionId, clean);
                  setEditing(null);
                } else if (e.key === "Escape") {
                  setEditing(null);
                }
              }}
              onBlur={(e) => {
                const clean = e.target.value.trim();
                if (clean && clean !== label) onRename?.(m.sessionId, clean);
                setEditing(null);
              }}
            />
          ) : (
            <span className="name">{label}</span>
          )}
        </button>

        <button
          type="button"
          className="session-menu-btn"
          aria-label={`Actions for ${label}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(e) => {
            e.stopPropagation();
            setMenuSessionId((cur) => (cur === m.sessionId ? null : m.sessionId));
          }}
        >
          <IconMore size={15} />
        </button>

        {menuOpen ? (
          <div className="session-row-menu" role="menu" aria-label={`Actions for ${label}`}>
            <button
              type="button"
              className="session-menu-item"
              role="menuitem"
              onClick={() => { onTogglePin?.(m.sessionId); setMenuSessionId(null); }}
            >
              <IconPin size={15} /> {m.pinned ? "Unpin" : "Pin"}
            </button>
            <button
              type="button"
              className="session-menu-item"
              role="menuitem"
              onClick={() => { setEditing(m.sessionId); setMenuSessionId(null); }}
            >
              <IconPencil size={15} /> Rename
            </button>
            <button
              type="button"
              className="session-menu-item danger"
              role="menuitem"
              onClick={() => { onDelete?.(m.sessionId); setMenuSessionId(null); }}
            >
              <IconTrash size={15} /> Delete
            </button>
          </div>
        ) : null}
      </li>
    );
  };

  return (
    <aside className={`sidebar${open ? " open" : ""}`} aria-label="Chats and navigation">
      {/* A real <button>: Space now works, and the branding is keyboard
          reachable without a role/tabIndex workaround. */}
      <button type="button" className="brand" onClick={() => go("workspace")} title="Back to the research console">
        <BrandLockup size="md" />
      </button>

      <nav className="side-nav" aria-label="Primary">
        <button type="button" className="new-btn" onClick={() => { onNew(); onClose?.(); }}>
          <IconPlus size={15} /> New research
        </button>
        {NAV.map((item) => (
          <button
            type="button"
            key={item.id}
            className={view === item.id ? "active" : ""}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => go(item.id)}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="side-section">
        {missions.length === 0 ? (
          <p className="empty">No research yet — start a question and the chat appears here.</p>
        ) : (
          <ul className="session-list">
            {missions.map(renderRow)}
          </ul>
        )}
      </div>
    </aside>
  );
}
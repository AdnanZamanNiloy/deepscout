import { IconAgents, IconCompass, IconMissions, IconPlus } from "./icons";
import BrandLockup from "./Brand";

/* Primary navigation + the session list.
 *
 * The list is deliberately FLAT: a small status dot and the title, one compact
 * single-line row per research session. An earlier version stacked mode and
 * "7 min ago" under every title and gave each row a menu button, which made
 * the rail harder to scan than the chat it points at — the sub-line competed
 * with the titles, and rows were twice as tall for information that belongs on
 * the session itself.
 *
 * Session management (rename / pin / delete) is NOT here: it lives on the
 * session's own header menu, so the rail stays a pure index of what exists.
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
  open, onClose,
}) {
  const go = (id) => { onNavigate(id); onClose?.(); };

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
            {missions.map((m) => {
              const label = m.title || m.query;
              const isActive = m.sessionId === activeSessionId;
              return (
                <li key={m.sessionId}>
                  <button
                    type="button"
                    className={`session-row${isActive ? " active" : ""}`}
                    aria-current={isActive ? "true" : undefined}
                    onClick={() => { onOpenMission(m.sessionId); onClose?.(); }}
                    title={label}
                  >
                    <span className={`dot ${STATUS_DOT[m.status] || "idle"}`} />
                    <span className="name">{label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
import { useState } from "react";
import { Copy, Eye, KeyRound, Unplug } from "lucide-react";
import { api } from "./lib";
import { Notice } from "./common";
import { pilotRu as t, ru } from "./ru";

export function SourceControls({
  base,
  enabled,
  refresh,
}: {
  base: string;
  enabled: boolean;
  refresh: () => void;
}) {
  const [action, setAction] = useState<"rotate" | "disable" | null>(null);
  const [busy, setBusy] = useState(false),
    [token, setToken] = useState(""),
    [show, setShow] = useState(false);
  const [message, setMessage] = useState(""),
    [error, setError] = useState("");
  async function submit() {
    if (!action || busy) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const reply = await api<{ upload_token?: string }>(
        base + "/source/" + action,
        {},
      );
      setToken(reply.upload_token ?? "");
      setShow(false);
      setMessage(action === "disable" ? t.disabledDone : "");
      setAction(null);
      refresh();
    } catch {
      setError(ru.networkError);
    } finally {
      setBusy(false);
    }
  }
  function begin(next: "rotate" | "disable") {
    setToken("");
    setAction(next);
    setMessage("");
    setError("");
    setShow(false);
  }
  return (
    <section className="panel source-section controls">
      <h2>{t.sourceAdmin}</h2>
      {!action && (
        <div className="admin-actions">
          <button
            className="button"
            type="button"
            onClick={() => begin("rotate")}
          >
            <KeyRound size={15} />
            {t.rotate}
          </button>
          {enabled && (
            <button
              className="button caution"
              type="button"
              onClick={() => begin("disable")}
            >
              <Unplug size={15} />
              {t.disable}
            </button>
          )}
        </div>
      )}
      {action && (
        <div className="admin-confirm">
          <p>{action === "rotate" ? t.rotateConfirm : t.disableConfirm}</p>
          <button
            type="button"
            className="button"
            onClick={submit}
            disabled={busy}
          >
            {action === "rotate" ? t.confirmRotate : t.confirmDisable}
          </button>
          <button
            type="button"
            className="text-btn"
            onClick={() => setAction(null)}
            disabled={busy}
          >
            {t.cancel}
          </button>
        </div>
      )}
      {token && (
        <div className="token-box">
          <p>{t.tokenHint}</p>
          <label>
            {t.token}
            <input
              type={show ? "text" : "password"}
              value={token}
              readOnly
              spellCheck={false}
              autoComplete="off"
              onFocus={(e) => e.target.select()}
            />
          </label>
          <div className="admin-actions">
            <button
              className="button"
              type="button"
              onClick={() => setShow((s) => !s)}
              aria-pressed={show}
            >
              <Eye size={15} />
              {show ? t.hide : t.show}
            </button>
            <button
              className="button"
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(token);
                  setMessage(t.copied);
                } catch {
                  setMessage(t.copyFailure);
                }
              }}
            >
              <Copy size={15} />
              {t.copy}
            </button>
            <button
              className="text-btn"
              type="button"
              onClick={() => {
                setToken("");
                setShow(false);
                setMessage("");
              }}
            >
              {t.dismissToken}
            </button>
          </div>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {error && <Notice>{error}</Notice>}
    </section>
  );
}

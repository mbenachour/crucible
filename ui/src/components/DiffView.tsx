import { Copy } from "./bits";

/** Render a unified diff with add/del/hunk colouring. No dependency. */
export function DiffView({ patch }: { patch: string }) {
  if (!patch?.trim()) return <div className="dim">No patch.</div>;
  const lines = patch.replace(/\r\n?/g, "\n").split("\n");
  return (
    <div>
      <Copy text={patch} label="copy patch" />
      <div className="diff">
        {lines.map((ln, i) => {
          let cls = "line";
          if (ln.startsWith("@@")) cls += " hunk";
          else if (/^\+\+\+ |^--- |^diff |^index /.test(ln)) cls += " meta";
          else if (ln.startsWith("+")) cls += " add";
          else if (ln.startsWith("-")) cls += " del";
          return (
            <span key={i} className={cls}>
              {ln || " "}
            </span>
          );
        })}
      </div>
    </div>
  );
}

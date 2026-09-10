import { Copy } from "./bits";

export function JsonView({ value }: { value: unknown }) {
  const text = JSON.stringify(value, null, 2);
  return (
    <div>
      <Copy text={text} label="copy json" />
      <pre className="code">{text}</pre>
    </div>
  );
}

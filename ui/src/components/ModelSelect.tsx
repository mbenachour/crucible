import { useConfigCatalog } from "../api/hooks";

const FAMILY_LABEL: Record<string, string> = { deepseek: "DeepSeek", qwen: "Qwen" };

/** A model id dropdown grouped by family (issue #80) — every role is
 * OpenRouter, so there's no provider field anywhere near this; picking a
 * model here can never produce an unresolvable id (unlike free text). */
export function ModelSelect({
  value,
  onChange,
  ariaLabel,
  style,
  disabled,
}: {
  value: string;
  onChange: (modelId: string) => void;
  ariaLabel: string;
  style?: React.CSSProperties;
  disabled?: boolean;
}) {
  const catalog = useConfigCatalog();
  const families = catalog.data?.families ?? {};
  const known = Object.values(families).some((models) => models.some((m) => m.id === value));

  return (
    <select
      aria-label={ariaLabel}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={style}
      disabled={disabled}
    >
      {/* keeps an unrecognized effective value (e.g. set outside the catalog
          via config.yaml) selectable rather than silently jumping away from it */}
      {!known && value && <option value={value}>{value}</option>}
      {Object.entries(families).map(([family, models]) => (
        <optgroup key={family} label={FAMILY_LABEL[family] ?? family}>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

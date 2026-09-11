import type { CoverageCell } from "../api/types";

export function cellState(c: CoverageCell): "productive" | "covered" | "none" {
  if (c.productive || c.findings > 0) return "productive";
  if (c.passes > 0) return "covered";
  return "none";
}

export function CoverageMatrix({ cells }: { cells: CoverageCell[] }) {
  const areas = Array.from(new Set(cells.map((c) => c.area))).sort();
  const classes = Array.from(new Set(cells.map((c) => c.attack_class))).sort();
  const byKey = new Map(cells.map((c) => [`${c.area}::${c.attack_class}`, c]));

  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="rowhdr">area \ class</th>
            {classes.map((k) => (
              <th key={k} className="colhdr">{k}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {areas.map((a) => (
            <tr key={a}>
              <td className="rowhdr">{a}</td>
              {classes.map((k) => {
                const c = byKey.get(`${a}::${k}`);
                if (!c) return <td key={k} className="cell-none" />;
                const st = cellState(c);
                return (
                  <td
                    key={k}
                    className={`cell-${st}${c.shallow ? " cell-shallow" : ""}`}
                    title={`${a} × ${k}\npasses: ${c.passes}  findings: ${c.findings}${
                      c.shallow ? "\nshallow" : ""
                    }`}
                  >
                    {c.findings || (c.passes ? "·" : "")}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

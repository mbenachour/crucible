import { useMemo } from "react";
import { marked } from "marked";

// marked with no extensions and no HTML passthrough is safe enough for our own
// generated docs; still, disable raw HTML so a finding description can't inject.
marked.setOptions({ gfm: true, breaks: false });

export function Markdown({ src }: { src: string }) {
  const html = useMemo(() => {
    const raw = marked.parse(src ?? "", { async: false }) as string;
    // strip <script>/<style> and on* attributes defensively
    return raw
      .replace(/<\/?(script|style)[^>]*>/gi, "")
      .replace(/ on\w+="[^"]*"/gi, "");
  }, [src]);
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

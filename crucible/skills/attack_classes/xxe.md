---
name: xxe
version: 0.1.0
description: An XML parser resolves attacker-controlled external entities, reaching file read, SSRF, or DoS.
languages: [py, js, go, ruby, php, java]
builtin: true
---

# Methodology

Load this body only when a Hunt task is scoped to `xxe` (§7).

## Attacker & boundary
Default attacker: the supplier of an XML document or an XML-backed format
(SOAP, SAML, RSS/Atom, SVG, DOCX/XLSX/ODF, XML config upload, `.xliff`).
Boundary crossed: untrusted XML → entity resolver → filesystem / network / the
parser's memory. Assumption broken: "the parser only reads the elements we
expect".

## Where to look
- `lxml.etree` with `resolve_entities=True` / a custom resolver, `xml.dom`,
  `xml.sax`, `xml.etree` on old runtimes, `defusedxml` *not* used
- Java `DocumentBuilderFactory` / `SAXParserFactory` / `XMLInputFactory`
  without `disallow-doctype-decl` / external-entity features disabled
- `libxml2` with `XML_PARSE_NOENT` / `XML_PARSE_DTDLOAD`
- second-order: XML built from input then re-parsed downstream
- billion-laughs / quadratic-blowup entity expansion (DoS variant)

## Move into execution (§9.2)
Feed the smallest parse call a doc with a `<!DOCTYPE>` defining
`<!ENTITY x SYSTEM "file:///etc/hostname">` and reference `&x;`; assert the
file content appears in the parsed result or an error that proves resolution
was attempted. For DoS, a nested-entity doc and a wall-clock / memory ceiling
hit in the sandbox.

## PoC shape
`poc_test` FAILS clean (external file content reflected, or expansion blows the
limit) and PASSES patched (entity resolution disabled / `defusedxml` /
`disallow-doctype-decl`). No source edits outside the patch (§9.4).

## Output
`threat_model.boundary_crossed`: "untrusted XML → entity resolution". Severity
`high` for file read / internal SSRF, `medium` for entity-expansion DoS.

## Anti-patterns to reject in your own output
- parser already has DTD / external entities disabled (check the actual flags)
- the "XML" is produced entirely server-side from trusted data
- JSON / a non-XML format misfiled under this class

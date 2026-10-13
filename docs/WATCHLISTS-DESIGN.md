---
name: Midas Watchlists
description: Saved research lists within the existing Market workspace
colors:
  primary: "#e0bd4b"
  primary-hover: "#e6ca6e"
  primary-ink: "#1b1913"
  primary-soft: "rgba(224, 189, 75, 0.14)"
  ground: "#1c1a14"
  surface: "#24221b"
  elevated: "#2f2d25"
  border: "#433f35"
  border-hover: "#6a6658"
  text: "#eceae1"
  muted: "#bebbb4"
  dim: "#a6a49b"
  positive: "#4a9e6d"
  negative: "#c4463a"
typography:
  headline:
    fontFamily: "Cinzel, Iowan Old Style, Charter, Georgia, Liberation Serif, Noto Serif, Times New Roman, serif"
    fontSize: "30px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Source Sans 3, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Noto Sans, Liberation Sans, Arial, sans-serif"
    fontSize: "13px"
    lineHeight: 1.5
  title:
    fontFamily: "Source Sans 3, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Noto Sans, Liberation Sans, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 650
    letterSpacing: "-0.01em"
  label:
    fontFamily: "Source Sans 3, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Noto Sans, Liberation Sans, Arial, sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
  help:
    fontSize: "12px"
rounded:
  chip: "6px"
  control: "8px"
  panel: "10px"
spacing:
  control: "5px 12px"
  field: "5px 9px"
  panel: "12px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-ink}"
    rounded: "{rounded.control}"
    padding: "{spacing.control}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "{spacing.control}"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "{spacing.field}"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.panel}"
    padding: "{spacing.panel}"
  provenance:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    rounded: "{rounded.chip}"
    padding: "1px 7px"
  navigation-active:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "40px"
    padding: "0 12px"
---

# Design System: Midas Watchlists

## Overview

**Creative North Star: "Existing Market workspace"**

This is an extraction of the current implementation, not a new brand proposal. Watchlists extend the existing charcoal and gold Operate surface: saved sets, filters, actual observations and source information are arranged for repeated research work. The old Market search, comparison and series tools remain below the new panel.

The descriptive language here is inferred from the supplied implementation. There is no formal PRODUCT document or confirmed brand metaphor. Luis explicitly authorised autonomous decisions; the scoped extraction therefore does not invent one or replace the incumbent world. Tokens are normative for this documented scope; shared theme generation remains the runtime authority.

**Key Characteristics:**
- Warm charcoal surfaces with a single gold action accent.
- Compact sans text, serif page heading and tabular numbers.
- Named lists and original series data have separate lifecycles.
- Complete source information remains available in every result row.

Evidence boundary: source `2038d2d9a6120b8c7232d991d02781e2c7d6693a`, submission revision 1. Read files: `client/src/components/Watchlists.jsx`, `client/src/pages/Market.jsx`, `client/src/components/ui.jsx`, `client/src/index.css`, `client/src/hoard-theme.css`, `client/src/copilot-context.js`, `midas_hoard/watchlists.py`. The requested Market path under components does not exist; its actual path is under pages. Source was clean when inspected. Screens supplied by the owner were inspected directly: `midas-desktop-final.png`, `midas-mobile-final.png`, `midas-mobile-results.png` in `D:/LocalAI/research-inbox/referencias-ampliacion-20261013/screens/`. The final screenshot is a scrolled results view, not a full-page capture. No browser, renderer, server or functional tests were run by this documenter; no Main activation is asserted.

## Colors

### Primary

Gold identifies the primary save action, active series options, source chips, brand and control focus. The stronger gold appears on primary hover. These roles come from the Midas-specific shared palette and the later, unlayered Hoard overrides in `index.css`.

### Neutral

Ground, surface and elevated layers separate the page, panel and navigation state. Warm pale text is the default; muted text identifies labels and support copy. Thin borders define controls and panel edges without decorative framing.

### Semantic

Positive and negative values inherit family success and danger colours. They are displayed alongside signed numeric values, not as a replacement for the number. A missing or unsupported metric is an em dash.

**The Source Rule.** Show the currency or unit, frequency, observation dates and source identity beside the observation; do not present rows as if they were converted into one common unit.

## Typography

The page heading uses the shared serif stack. Body text and the panel heading use the shared sans stack; controls are compact and semibold. The final unlayered font declaration wins over the earlier Segoe UI base declaration. Shared CSS declares families, not a guarantee that every font is installed.

The headline, title, label and help roles are extracted above. Controls use 12.5px text; table headers use 11.5px semibold text. Data tables use tabular numerals. The existing metadata utility uses Cascadia Mono/Consolas; do not infer that every number or chip uses a monospace face.

## Layout

At desktop width the new full-width panel precedes the incumbent Market workspace. Within it the operational order is heading and refresh action; list selector and name; save/rename/add actions; filters; sort and result count; results; pagination; metric explanation. The six filters use two columns at the base viewport, three at the `sm` breakpoint and six at `lg`. Selector and name stack initially and share two columns at `sm`. The supplied compiled stylesheet confirms `sm` at 40rem and `lg` at 64rem. The app's established desktop sidebar switches at 768px and uses the shared 220px width.

Actions wrap without dropping options. Results retain all six columns within their own horizontal scrolling container on narrow screens. The supplied mobile views demonstrate this adaptation at 390×844. The owner reports document width 380px and panel width 348px; those DOM measurements were not independently rerun here. The mobile navigation's horizontal scrolling is incumbent app behaviour, not a new watchlist rule.

Panel padding follows the compact 12px system. Watchlist form groups use the existing Tailwind gap utilities, not a new spacing scale. Avoid imposing a hero, decorative tile grid or a new navigation shell on this task surface.

## Elevation & Depth

Watchlists are flat at rest: tone and a one-pixel border provide separation. The panel has no shadow. Existing transient toasts use the family ambient shadow documented in the sidecar; do not apply that shadow to every result row or panel. Button state transitions use the incumbent 0.14s background and border-colour change, disabled by reduced-motion preference.

## Shapes

Controls and panels use the shared modest radius scale; the final family overrides win over earlier local radii. Provenance chips are compact rounded rectangles, not fully rounded pills. Rows retain conventional table lines and aligned content. Native selects and date inputs preserve platform affordances.

## Components

### Save, rename, add and refresh

Saving the selected series as a new list is the primary gold action. Rename and add-to-current-list are secondary actions. Refresh is placed next to the panel heading and appears only with an active list. Busy state disables write controls; blank name and empty selection disable the corresponding action. Selection in Market is independent of the saved list's membership.

### Saved list selector and name

Saved records persist in the database and populate the selector after mount. The selector's initial value is an empty string: reload returns to “New watchlist”, then the user chooses a saved record. This is persistence of records, **not** automatic restoration of the previously active selection. Choosing a list clears transient results, pagination history and update outcome. The dirty name draft is protected when the list index refreshes; rename requires a changed, nonblank name.

### Result table and provenance

The six columns are series, last value, change, drawdown, period and source. The series button opens the existing series viewer. “Remove from watchlist” alters membership; the backend save path updates list JSON and does not delete source snapshot files or records. Source chips display provider, symbol, actual ending date and a shortened SHA. Their native title contains the full ID, fetch time, actual range, currency/unit, adjustment status and full SHA. The title's presence is source-confirmed; this does not certify full keyboard or touch access to tooltip details.

Metric semantics are source-confirmed: values are numeric, finite observations capped to `lookback + 1`, with optional as-of cutoff. Change percentage requires a positive first value and at least two observations. Drawdown uses the cumulative peak and requires all observations to be positive. Every row retains its original currency/unit, frequency and actual dates; no exchange conversion is performed. Missing values remain uncomputed. A minimum-change filter excludes absent percentages; maximum-drawdown compares the absolute loss. Filter changes reset pagination.

### Loading, errors and updates

The section declares a heading and `aria-busy`; loading and update outcomes have `role="status"`. ErrorBox is reused. Native fields are contained within real labels, and the incumbent focus treatment is reused. Empty filtered results have an explicit message. A monotonic request counter keeps late views from replacing newer filter results; runtime race behaviour was not re-executed by the documenter.

The backend refresh captures members, fetches individually, keeps CSV entries pinned and returns partial errors. It then rereads current membership in a transaction, applying replacements only when provider, case-folded symbol and original snapshot still match. Concurrent rename, add, removal or replacement therefore remain intact in the inspected code. `attached` describes whether a successful downloaded snapshot is still attached after publication. The UI shows an error count, not every per-entry result. These are source-supported contracts; the author packet reports real private MCP calls and concurrent-area tests, while this files-only handoff does not repeat them or certify market connectivity.

### Faustus context

The active list and filters publish view context; the form publishes ID, name, dirty state and selected snapshot IDs as a draft. The copilot context captures and copies that draft before its first asynchronous lookup. The lookup is tied to the captured identity, so context must not silently become another view when the user types or navigates. Source confirms that snapshot construction; author evidence reports 24 JavaScript tests including the mutable-draft regression. No live LLM behaviour is claimed here.

## Do's and Don'ts

### Do:
- **Do** keep saved-list membership independent of the Market selection.
- **Do** retain all result columns and source details inside the mobile table scroller.
- **Do** preserve existing snapshot data when removing a list member.
- **Do** describe saved records as persistent and active selection as explicit after reload.
- **Do** preserve partial-refresh outcomes and concurrent member edits.
- **Do** capture draft context before asynchronous lookups.

### Don't:
- **Don't** imply the rows have a common currency, calendar frequency or converted unit.
- **Don't** calculate percentages without the positive bases required by the source.
- **Don't** treat an uncomputed metric as zero.
- **Don't** replace this existing Operate world with a new visual identity.
- **Don't** describe supplied screenshots or author receipts as tests rerun by this documenter.

The matching schema-2 sidecar contains eight isolated component samples and extension metadata. Synthesised colour ramps are display aids rather than new runtime tokens. Finish handoff is `disposition: ship` for this bounded surface. No additional refinement is introduced here.

Sidecar in this repository: `docs/design/watchlists.design.json`.

R2 functional amendment: the source is now8edf829a over2038d2d9. Overflowing derived metrics return null with a per-row explanation, while source values and neighboring rows remain available. The UI adds that explanation beside provenance and fixes Spanish singular agreement. The reviewed charcoal/gold world, layout and controls remain the same; the original extraction and captures describe R1, with this amendment recording the later scoped changes.

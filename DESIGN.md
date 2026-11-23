# Sova blood-test panel: design system

An internal operations panel used all day by the Sova ops team, plus a few customer pages (booking, confirmation, report). The direction is calm, clinical and dense: data first, one brand colour, no decoration. Every value below is a CSS variable in `ui.py` (`CSS`), so pages use names and never raw values.

This direction comes from the existing Sova brand colours and the panel's own pages. It was not compared against reference sites (UXKIN wasn't connected).

## Colour

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | #f4f8f9 | #0b1518 | Page background |
| `--card` | #ffffff | #111e22 | Cards, tables, header, inputs |
| `--fg` | #10262e | #e2edf0 | Body text, headings |
| `--mute` | #566b73 | #9cb0b7 | Secondary text, labels, inactive tabs |
| `--line` | #d8e3e7 | #25373d | Borders and dividers |
| `--muteb` | #edf2f4 | #1a292e | Table header, hover rows, quiet fills |
| `--pri` | #0e7490 | #38bdd8 | The one action colour: primary buttons, links, active tab, focus ring |
| `--ok` / `--okb` | #047857 / #e3f5ee | #4ade9b / #0f2e24 | Done, sent, live |
| `--warn` / `--warnb` | #9a4a07 / #fdf0dc | #f6b562 / #33240f | Needs attention, test mode, placeholders |
| `--bad` / `--badb` | #b42318 / #fde8e6 | #ff8a80 / #3a1714 | Failed, blocked, destructive |
| `--info` / `--infob` | #1e4fb8 / #e6eefc | #9cc0ff / #15233d | Neutral notices, "to send" |

There is no accent colour and there are no gradients. Status is always shown with words in a chip, never with colour alone. Text contrast meets 4.5:1 on its own background.

## Typography

The system UI font stack (`system-ui, -apple-system, Segoe UI, Roboto`), with one family for everything. Numbers use `tabular-nums`.

| Use | Size / line height | Weight |
|---|---|---|
| Page title (h1) | 24 / 1.25 | 700 |
| Section title (h2) | 17 / 1.3 | 600 |
| KPI number | 26 | 700 |
| Body | 15 / 1.5 | 400 |
| Tabs, table cells, buttons | 14 | 500–600 |
| Labels, chips, help text | 13 | 600 / 400 |
| Table headers, field labels, group labels | 12, uppercase, +0.04em | 600 |

The body is 15px, not 16px, on purpose: the panel is table-heavy and used on laptops. Customer pages (`.book`) use 16px controls.

## Spacing and layout

- **Scale:** 4px base, `--s1` 4 · `--s2` 8 · `--s3` 12 · `--s4` 16 · `--s5` 20 · `--s6` 24 · `--s8` 32.
- **Content width:** `--wmax` 1240px, centred.
- **Page padding:** 24px top, 20px sides, 64px bottom. On phones it is 20 / 16 / 32.
- **Alignment:** the header brand, the first tab's text and the page content all start on the same 20px edge.
- **Gaps:** 16px between stacked sections, 12px inside grids, 12–16px between form fields.

## Shape and depth

- **Radius:** `--r-sm` 8 (inputs, buttons) · `--r` 10 (menus, banners, small cards) · `--r-lg` 12 (cards, tables) · `--pill` (chips, filter pills, sign out).
- **Shadows:** two only. `--sh1` is a hairline lift for cards, tables and the header. `--sh2` is for floating menus and dialogs.

## Components

- **Header:** sticky. The top row holds the brand (logo, "Sova", "Program blood tests"), the send-mode chip, and the account (initial, email, Sign out). The second row is the tabs: Overview · BT dashboard · Not booked · Send WhatsApp · Analytics · Legacy data | SKUs · Settings. A Downloads menu on the right holds the CSV exports. The active tab gets a 2px `--pri` underline and `aria-current=page`. On phones the tabs scroll sideways with a fade at the edge.
- **Buttons:**
  - Primary is filled `--pri`.
  - Ghost is transparent with a `--line` border and `--pri` text, used for secondary actions.
  - Danger is filled `--bad`.
  - Every button is at least 40px high (48px on customer pages), slightly brighter on hover, and shows "Please wait…" while submitting.
- **Inputs:** 40px high with a `--line` border. The border turns `--mute` on hover and `--pri` on focus, and there is a 3px focus ring.
- **Field rows (`.btc` / `.f`):** fields wrap side by side, each with a 12px uppercase label above it. On phones each field takes the full width.
- **Cards:** `--card` with a `--line` border, 20px padding and `--sh1`. KPI cards (`.cardb`) add a 4px coloured left edge for their status group.
- **Tables:** live in `.wrap`, which scrolls sideways. The header row is sticky on `--muteb` with uppercase 12px labels. Rows highlight on hover. Cells have 10–12px padding.
- **Chips and filter pills:** chips carry status; pills filter, and the selected pill is filled `--pri`.
- **Banners:** a tinted background with a 4px left border in the text colour, for success, warning, error or info.

## Imagery and icons

No illustrations, stock photos or emoji. The Sova logo comes from `logo.webp`; if it's missing, a teal "S" mark appears in its place. Arrows and carets are text glyphs (↓ ▾ →).

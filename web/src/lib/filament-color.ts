/**
 * Bambu MQTT reports tray_color as 8-hex "RRGGBBAA" (alpha included) or "".
 * Empty / fully-transparent / "00000000" means no filament loaded.
 * Returns "#RRGGBB" (alpha stripped) or null when there's no usable color.
 */
export function normalizeTrayColor(raw: string): string | null {
  if (!raw) return null;
  const hex = raw.trim().replace(/^#/, '');
  if (hex.length < 6) return null;
  if (/^0+$/.test(hex)) return null; // all zeros = empty slot
  // Validate hex chars
  if (!/^[0-9a-fA-F]+$/.test(hex)) return null;
  return `#${hex.slice(0, 6).toUpperCase()}`;
}

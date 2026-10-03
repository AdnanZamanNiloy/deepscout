import { useState } from "react";

/* The DeepScout identity — one lockup for the landing page, the chat sidebar and
 * the docs site.
 *
 * Drawn in code rather than shipped as an image, for three reasons:
 *
 *  1. The mark is theme-aware. The brand artwork's inner disc is a solid fill
 *     the designer labelled "change fill to restyle" — it is the page ground
 *     showing through. Reproducing it in CSS means one component is correct in
 *     both themes, where a shipped PNG/SVG would need two variants.
 *  2. The wordmark stays real text: selectable, themeable, and translatable.
 *     The two-tone treatment ("Deep" in the text colour, "Scout" in the brand
 *     gradient) is applied with gradient clipping, which an outlined-path
 *     wordmark cannot do responsively.
 *  3. No baked-in provenance. The signed brand SVGs remain the source of truth
 *     for print and export; this is the in-app rendering of the same artwork.
 *
 * The aperture blades and hexagon below are the brand geometry, transcribed
 * from the signed mark so the silhouette is exact rather than approximated.
 */

const BLADES = [
  "M517.0 49.0L279.0 461.3L144.9 229.0A463.5 463.5 0 0 1 517.0 49.0Z",
  "M915.5 284.8L439.4 284.9L573.5 52.6A463.5 463.5 0 0 1 915.5 284.8Z",
  "M910.5 747.8L672.4 335.6L940.6 335.6A463.5 463.5 0 0 1 910.5 747.8Z",
  "M507.0 975.0L745.0 562.7L879.1 795.0A463.5 463.5 0 0 1 507.0 975.0Z",
  "M108.5 739.2L584.6 739.1L450.5 971.4A463.5 463.5 0 0 1 108.5 739.2Z",
  "M113.5 276.2L351.6 688.4L83.4 688.4A463.5 463.5 0 0 1 113.5 276.2Z",
];

const HEXAGON = "M590.5 512.0L551.2 580.0L472.8 580.0L433.5 512.0L472.7 444.0L551.2 444.0Z";

let seq = 0;

/** Gradient ids must be unique: two lockups on a page would share one def. */
function useGradientId() {
  const [id] = useState(() => `dsBrand${(seq += 1)}`);
  return id;
}

export function BrandMark({ size = 34, title = "DeepScout" }) {
  const id = useGradientId();
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 1024 1024"
      role="img"
      aria-label={title}
    >
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="240" y1="90" x2="880" y2="960">
          <stop offset="0" stopColor="#FDA46A" />
          <stop offset="0.5" stopColor="#EE7C50" />
          <stop offset="1" stopColor="#D2461F" />
        </linearGradient>
      </defs>
      {/* The disc is the page showing through, so it follows the theme. */}
      <circle cx="512" cy="512" r="471" className="brand-mark-disc" />
      <g fill={`url(#${id})`} stroke={`url(#${id})`} strokeWidth="18" strokeLinejoin="round">
        {BLADES.map((d) => (
          <path key={d} d={d} />
        ))}
        <path d={HEXAGON} />
      </g>
    </svg>
  );
}

export default function BrandLockup({ size = "md", className = "" }) {
  return (
    <span className={`brand-lockup ${className}`.trim()} data-size={size}>
      <BrandMark size={size === "lg" ? 40 : size === "sm" ? 22 : 30} />
      <span className="brand-word">
        {/* Two-tone wordmark: "Deep" carries the theme's text colour, "Scout"
            takes the brand gradient. Kept as one string for screen readers. */}
        <span className="brand-name" aria-label="DeepScout">
          <span className="brand-name-deep" aria-hidden="true">Deep</span>
          <span className="brand-name-scout" aria-hidden="true">Scout</span>
        </span>
      </span>
    </span>
  );
}
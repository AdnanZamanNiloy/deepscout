/* The real MARS brand lockup — planet mark plus the MARS wordmark and
 * "Multi-Agent Research System", straight from the signed brand asset.
 *
 * Previously all three surfaces drew the brand in CSS (a gradient sphere plus
 * text spans), which is why the landing page, the docs header and the console
 * sidebar each looked like a different product from the official artwork.
 *
 * Two variants ship, because the artwork bakes its background in: the dark
 * variant is for dark surfaces (white wordmark) and the light one for light
 * surfaces (near-black wordmark). Which is shown is decided in CSS from
 * `data-theme`, so there is no flash of the wrong one on first paint and no
 * JavaScript in the path.
 */
export default function BrandLockup({ className = "", height = 26 }) {
  return (
    <span className={`brand-lockup ${className}`.trim()} style={{ height }}>
      <img
        className="brand-lockup-img brand-lockup-dark"
        src="/mars-branding-dark.svg"
        alt=""
        aria-hidden="true"
        height={height}
      />
      <img
        className="brand-lockup-img brand-lockup-light"
        src="/mars-branding-light.svg"
        alt=""
        aria-hidden="true"
        height={height}
      />
    </span>
  );
}

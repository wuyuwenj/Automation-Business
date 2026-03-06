import { useEffect, useRef } from "react";

/**
 * ZeroClick ad banner component.
 *
 * Renders a ZeroClick ad placement in the buyer frontend.
 * Revenue from ads offsets the cost of purchasing from sellers.
 *
 * Configure by setting VITE_ZEROCLICK_PLACEMENT_ID in your .env file.
 * If not set, shows a placeholder explaining the ad slot.
 */

const PLACEMENT_ID = import.meta.env.VITE_ZEROCLICK_PLACEMENT_ID || "";
const ZEROCLICK_SCRIPT = "https://sdk.zeroclick.ai/v1/zeroclick.js";

export default function AdBanner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const scriptLoaded = useRef(false);

  useEffect(() => {
    if (!PLACEMENT_ID || scriptLoaded.current) return;

    // Load ZeroClick SDK script
    const existing = document.querySelector(
      `script[src="${ZEROCLICK_SCRIPT}"]`,
    );
    if (!existing) {
      const script = document.createElement("script");
      script.src = ZEROCLICK_SCRIPT;
      script.async = true;
      script.dataset.placementId = PLACEMENT_ID;
      document.head.appendChild(script);
    }
    scriptLoaded.current = true;
  }, []);

  if (!PLACEMENT_ID) {
    return (
      <div className="mx-3 mb-3 rounded-lg border border-dashed border-zinc-700 bg-zinc-900/50 p-3 text-center">
        <p className="text-xs text-zinc-500">
          Ad slot — set <code className="text-zinc-400">VITE_ZEROCLICK_PLACEMENT_ID</code> to enable ZeroClick ads
        </p>
        <p className="mt-1 text-[10px] text-zinc-600">
          Revenue offsets credit costs
        </p>
      </div>
    );
  }

  return (
    <div className="mx-3 mb-3">
      <div
        ref={containerRef}
        data-zeroclick-placement={PLACEMENT_ID}
        className="min-h-[60px] rounded-lg overflow-hidden"
      />
      <p className="mt-1 text-center text-[10px] text-zinc-600">
        Sponsored — offsets credit costs
      </p>
    </div>
  );
}

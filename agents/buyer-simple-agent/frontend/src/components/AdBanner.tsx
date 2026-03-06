import { useEffect, useRef, useState } from "react";

/**
 * ZeroClick ad banner component.
 *
 * Renders a ZeroClick ad placement in the buyer frontend.
 * Revenue from ads offsets the cost of purchasing from sellers.
 *
 * The placement ID is loaded at runtime from the backend /api/config
 * endpoint, which reads it from environment variables or Secrets Manager.
 */

const ZEROCLICK_SCRIPT = "https://sdk.zeroclick.ai/v1/zeroclick.js";

export default function AdBanner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const scriptLoaded = useRef(false);
  const [placementId, setPlacementId] = useState("");

  useEffect(() => {
    fetch("/api/config")
      .then((res) => res.json())
      .then((data) => {
        if (data.zeroclickPlacementId) {
          setPlacementId(data.zeroclickPlacementId);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!placementId || scriptLoaded.current) return;

    // Load ZeroClick SDK script
    const existing = document.querySelector(
      `script[src="${ZEROCLICK_SCRIPT}"]`,
    );
    if (!existing) {
      const script = document.createElement("script");
      script.src = ZEROCLICK_SCRIPT;
      script.async = true;
      script.dataset.placementId = placementId;
      document.head.appendChild(script);
    }
    scriptLoaded.current = true;
  }, [placementId]);

  if (!placementId) {
    return (
      <div className="mx-3 mb-3 rounded-lg border border-dashed border-zinc-700 bg-zinc-900/50 p-3 text-center">
        <p className="text-xs text-zinc-500">
          Ad slot — set <code className="text-zinc-400">ZEROCLICK_PLACEMENT_ID</code> in env to enable ZeroClick ads
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
        data-zeroclick-placement={placementId}
        className="min-h-[60px] rounded-lg overflow-hidden"
      />
      <p className="mt-1 text-center text-[10px] text-zinc-600">
        Sponsored — offsets credit costs
      </p>
    </div>
  );
}

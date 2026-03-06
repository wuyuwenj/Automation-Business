import { useEffect, useRef, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fetchConfig, fetchZeroClickOffers, type ZeroClickOffer } from "@/api";

const IMPRESSIONS_URL = "https://zeroclick.dev/api/v2/impressions";

export default function AdBanner() {
  const trackedIds = useRef<Set<string>>(new Set());
  const [enabled, setEnabled] = useState(false);
  const [query, setQuery] = useState("");
  const [offer, setOffer] = useState<ZeroClickOffer | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    fetchConfig()
      .then((data) => {
        if (!data?.zeroclickEnabled) {
          setEnabled(false);
          setIsLoading(false);
          return;
        }
        setEnabled(true);
        setQuery(data.zeroclickQuery || "AI tools for business");
      })
      .catch(() => {
        setEnabled(false);
        setIsLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!enabled || !query) return;

    let cancelled = false;
    setIsLoading(true);
    setLoadError("");

    fetchZeroClickOffers(query)
      .then((offers) => {
        if (cancelled) return;
        if (offers.length === 0) {
          setOffer(null);
          setLoadError("No offers available right now.");
        } else {
          setOffer(offers[0]);
        }
      })
      .catch(() => {
        if (cancelled) return;
        setOffer(null);
        setLoadError("Failed to load sponsored offer.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [enabled, query]);

  useEffect(() => {
    if (!offer || trackedIds.current.has(offer.id)) return;

    trackedIds.current.add(offer.id);
    fetch(IMPRESSIONS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: [offer.id] }),
    }).catch(() => {
      trackedIds.current.delete(offer.id);
    });
  }, [offer]);

  if (!enabled) {
    return (
      <div className="mx-3 mb-3 rounded-lg border border-dashed border-zinc-700 bg-zinc-900/50 p-3 text-center">
        <p className="text-xs text-zinc-500">
          Ad slot — set <code className="text-zinc-400">ZEROCLICK_API_KEY</code> in env to enable ZeroClick ads
        </p>
        <p className="mt-1 text-[10px] text-zinc-600">
          Revenue offsets credit costs
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="mx-3 mb-3 rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
        <p className="text-xs text-zinc-400">Loading sponsored offer...</p>
      </div>
    );
  }

  if (!offer) {
    return (
      <div className="mx-3 mb-3 rounded-lg border border-dashed border-amber-500/40 bg-amber-950/20 p-3 text-center">
        <p className="text-xs text-amber-200">
          {loadError || "Sponsored offer unavailable right now."}
        </p>
      </div>
    );
  }

  const price = offer.price?.amount && offer.price?.currency
    ? `${offer.price.currency} ${offer.price.amount}`
    : "";

  return (
    <div className="mx-3 mb-3 rounded-xl border border-zinc-800 bg-zinc-950/90 p-3 text-zinc-100">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Badge variant="secondary" className="border-0 bg-emerald-500/15 text-[10px] text-emerald-300">
          Sponsored
        </Badge>
        <span className="text-[10px] text-zinc-500">Offsets credit costs</span>
      </div>

      {offer.imageUrl && (
        <img
          src={offer.imageUrl}
          alt={offer.title}
          className="mb-3 h-28 w-full rounded-lg object-cover"
        />
      )}

      <div className="space-y-2">
        <div>
          <p className="text-sm font-semibold leading-tight">{offer.title}</p>
          {offer.subtitle && (
            <p className="mt-1 text-xs text-zinc-400">{offer.subtitle}</p>
          )}
        </div>

        {offer.content && (
          <p className="text-xs leading-5 text-zinc-300 line-clamp-3">
            {offer.content}
          </p>
        )}

        <div className="flex items-center justify-between gap-3 text-xs">
          <div className="min-w-0">
            {offer.brand?.name && (
              <p className="truncate text-zinc-400">{offer.brand.name}</p>
            )}
            {price && (
              <p className="font-medium text-zinc-100">{price}</p>
            )}
          </div>
          <a
            href={offer.clickUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-md bg-emerald-500 px-3 py-1.5 font-medium text-zinc-950 transition hover:bg-emerald-400"
          >
            {offer.cta || "Open"}
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>
    </div>
  );
}

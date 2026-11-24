"use client";

import * as React from "react";
import { Check, CreditCard as CardIcon } from "lucide-react";
import { fetchCards } from "@/lib/api";
import { CreditCard } from "@/lib/types";
import { Sheet } from "@/components/ui/Sheet";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";

interface CardWalletContextValue {
  cards: CreditCard[];
  selectedIds: string[];
  loading: boolean;
  sheetOpen: boolean;
  openWallet: () => void;
  closeWallet: () => void;
  toggleCard: (id: string) => void;
  selectAll: () => void;
  clearAll: () => void;
}

const CardWalletContext = React.createContext<CardWalletContextValue | null>(null);

export function useCardWallet(): CardWalletContextValue {
  const ctx = React.useContext(CardWalletContext);
  if (!ctx) {
    throw new Error("useCardWallet must be used inside CardWalletProvider");
  }
  return ctx;
}

const STORAGE_KEY = "onestop_user_card_ids";

export function CardWalletProvider({ children }: { children: React.ReactNode }) {
  const [cards, setCards] = React.useState<CreditCard[]>([]);
  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [sheetOpen, setSheetOpen] = React.useState(false);

  React.useEffect(() => {
    let active = true;
    async function init() {
      const allCards = await fetchCards();
      if (!active) return;
      setCards(allCards);

      try {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved !== null) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed)) {
            setSelectedIds(parsed);
          }
        }
      } catch {
        // ignore storage errors
      } finally {
        if (active) setLoading(false);
      }
    }
    init();
    return () => {
      active = false;
    };
  }, []);

  const persist = (ids: string[]) => {
    setSelectedIds(ids);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch {
      // ignore
    }
  };

  const toggleCard = (id: string) => {
    const next = selectedIds.includes(id)
      ? selectedIds.filter((c) => c !== id)
      : [...selectedIds, id];
    persist(next);
  };

  const selectAll = () => persist(cards.map((c) => c.id));
  const clearAll = () => persist([]);

  return (
    <CardWalletContext.Provider
      value={{
        cards,
        selectedIds,
        loading,
        sheetOpen,
        openWallet: () => setSheetOpen(true),
        closeWallet: () => setSheetOpen(false),
        toggleCard,
        selectAll,
        clearAll,
      }}
    >
      {children}

      <Sheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        title="My cards"
        description="Choose the cards you own so we only show discounts you can actually use."
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between text-[13px]">
            <span className="text-muted tabular-nums">
              {selectedIds.length} of {cards.length} selected
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={selectAll}
                className="min-h-[36px] px-2 text-[13px] font-medium text-accent hover:underline"
              >
                Select all
              </button>
              <button
                type="button"
                onClick={clearAll}
                className="min-h-[36px] px-2 text-[13px] font-medium text-muted hover:text-text"
              >
                Clear
              </button>
            </div>
          </div>

          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((n) => (
                <Skeleton key={n} className="h-14 w-full" />
              ))}
            </div>
          ) : cards.length === 0 ? (
            <div className="rounded-[12px] border border-border bg-surface-2 p-6 text-center space-y-2">
              <CardIcon strokeWidth={1.5} className="w-5 h-5 text-muted mx-auto" />
              <p className="text-[13px] text-muted">
                No cards available right now. Check that the backend is running.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {cards.map((card) => {
                const checked = selectedIds.includes(card.id);
                return (
                  <button
                    key={card.id}
                    type="button"
                    role="checkbox"
                    aria-checked={checked}
                    onClick={() => toggleCard(card.id)}
                    className={`w-full min-h-[52px] px-3.5 py-2.5 rounded-[8px] border text-left flex items-center justify-between gap-3 transition-colors duration-150 ${
                      checked
                        ? "border-accent bg-accent-subtle/60"
                        : "border-border bg-surface hover:bg-surface-2"
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="text-[14px] font-medium text-text truncate">
                        {card.card_name}
                      </div>
                      <div className="text-[12px] text-muted truncate">
                        {card.bank_name} · {card.card_network.toUpperCase()} ·{" "}
                        <span className="font-mono tabular-nums">{card.base_reward_pct}%</span> base
                      </div>
                    </div>
                    <span
                      className={`w-5 h-5 rounded-[6px] border flex items-center justify-center flex-shrink-0 transition-colors duration-150 ${
                        checked
                          ? "bg-accent border-accent text-[#FAF9F6] dark:text-[#121210]"
                          : "border-border bg-surface"
                      }`}
                    >
                      {checked && <Check strokeWidth={2} className="w-3.5 h-3.5" />}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="pt-2">
            <Button className="w-full" onClick={() => setSheetOpen(false)}>
              Done
            </Button>
          </div>
        </div>
      </Sheet>
    </CardWalletContext.Provider>
  );
}

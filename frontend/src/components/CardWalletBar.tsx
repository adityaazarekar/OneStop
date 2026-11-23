"use client";

import { useEffect, useState } from "react";
import { CreditCard as CardIcon, Check, ChevronDown, Plus, Sparkles } from "lucide-react";
import { CreditCard } from "@/lib/types";
import { fetchCards } from "@/lib/api";

interface Props {
  onCardsChanged?: (cardIds: string[]) => void;
}

export function CardWalletBar({ onCardsChanged }: Props) {
  const [cards, setCards] = useState<CreditCard[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function init() {
      const allCards = await fetchCards();
      setCards(allCards);

      // Restore saved cards from localStorage
      try {
        const saved = localStorage.getItem("onestop_user_card_ids");
        if (saved) {
          const parsed = JSON.parse(saved);
          if (Array.isArray(parsed) && parsed.length > 0) {
            setSelectedIds(parsed);
            onCardsChanged?.(parsed);
          } else if (allCards.length > 0) {
            // Default to top 2 popular Indian cards (HDFC Infinia, ICICI Amazon Pay)
            const defaults = allCards.slice(0, 3).map((c) => c.id);
            setSelectedIds(defaults);
            localStorage.setItem("onestop_user_card_ids", JSON.stringify(defaults));
            onCardsChanged?.(defaults);
          }
        } else if (allCards.length > 0) {
          const defaults = allCards.slice(0, 3).map((c) => c.id);
          setSelectedIds(defaults);
          localStorage.setItem("onestop_user_card_ids", JSON.stringify(defaults));
          onCardsChanged?.(defaults);
        }
      } catch (err) {
        console.warn(err);
      } finally {
        setLoading(false);
      }
    }
    init();
  }, []);

  const toggleCard = (id: string) => {
    let updated: string[];
    if (selectedIds.includes(id)) {
      updated = selectedIds.filter((cid) => cid !== id);
    } else {
      updated = [...selectedIds, id];
    }
    setSelectedIds(updated);
    localStorage.setItem("onestop_user_card_ids", JSON.stringify(updated));
    onCardsChanged?.(updated);
  };

  const selectAll = () => {
    const all = cards.map((c) => c.id);
    setSelectedIds(all);
    localStorage.setItem("onestop_user_card_ids", JSON.stringify(all));
    onCardsChanged?.(all);
  };

  const clearAll = () => {
    setSelectedIds([]);
    localStorage.setItem("onestop_user_card_ids", JSON.stringify([]));
    onCardsChanged?.([]);
  };

  return (
    <div className="w-full bg-surface-card/60 backdrop-blur border border-surface-border rounded-2xl p-4 transition-all">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-brand-primary/10 border border-brand-primary/20 flex items-center justify-center text-brand-primary">
            <CardIcon className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-bold text-white">My Card Wallet</span>
              <span className="text-xs px-2 py-0.5 rounded-full bg-brand-primary/20 text-brand-primary font-semibold">
                {selectedIds.length} Selected
              </span>
            </div>
            <p className="text-xs text-slate-400">
              Pick your active cards for instant effective price calculation & reward optimization
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 self-end sm:self-auto">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-surface border border-surface-border text-slate-200 hover:text-white hover:border-slate-500 transition-colors"
          >
            <span>{isOpen ? "Close Wallet" : "Edit Cards"}</span>
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${isOpen ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {/* Selected cards pill strip */}
      {!isOpen && (
        <div className="mt-3 flex items-center gap-2 overflow-x-auto pb-1 pt-1 scrollbar-none">
          {cards
            .filter((c) => selectedIds.includes(c.id))
            .map((card) => (
              <div
                key={card.id}
                className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-surface border border-surface-border text-slate-200 whitespace-nowrap"
              >
                <span className="text-brand-accent font-bold">
                  {card.amazon_reward_pct > 0 ? `${card.amazon_reward_pct}%` : `${card.base_reward_pct}%`}
                </span>
                <span>{card.card_name}</span>
              </div>
            ))}
          {selectedIds.length === 0 && (
            <span className="text-xs text-slate-500 italic">
              No cards selected. Click &quot;Edit Cards&quot; to pick your cards.
            </span>
          )}
        </div>
      )}

      {/* Expanded card drawer */}
      {isOpen && (
        <div className="mt-4 pt-4 border-t border-surface-border">
          <div className="flex items-center justify-between mb-3 text-xs">
            <span className="text-slate-400 font-medium">Select all cards in your possession:</span>
            <div className="flex items-center gap-3">
              <button onClick={selectAll} className="text-brand-primary hover:underline font-semibold">
                Select All
              </button>
              <button onClick={clearAll} className="text-slate-400 hover:underline">
                Clear
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 max-h-72 overflow-y-auto pr-1">
            {cards.map((card) => {
              const isSelected = selectedIds.includes(card.id);
              return (
                <div
                  key={card.id}
                  onClick={() => toggleCard(card.id)}
                  className={`p-3 rounded-xl border cursor-pointer transition-all flex items-start justify-between gap-2 select-none ${
                    isSelected
                      ? "bg-brand-primary/10 border-brand-primary/40 text-white shadow-sm shadow-brand-primary/10"
                      : "bg-surface/60 border-surface-border text-slate-400 hover:border-slate-600 hover:text-slate-200"
                  }`}
                >
                  <div className="min-w-0">
                    <p className="text-xs font-bold truncate leading-tight">{card.card_name}</p>
                    <p className="text-[10px] text-slate-500 mt-0.5">
                      {card.bank_name} · {card.card_network.toUpperCase()}
                    </p>
                    <div className="flex items-center gap-2 mt-1.5 text-[10px]">
                      <span className="text-emerald-400 font-semibold">
                        Base: {card.base_reward_pct}%
                      </span>
                      {card.amazon_reward_pct > card.base_reward_pct && (
                        <span className="text-amber-400 font-semibold">
                          Amazon: {card.amazon_reward_pct}%
                        </span>
                      )}
                      {card.flipkart_reward_pct > card.base_reward_pct && (
                        <span className="text-blue-400 font-semibold">
                          Flipkart: {card.flipkart_reward_pct}%
                        </span>
                      )}
                    </div>
                  </div>

                  <div
                    className={`w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 transition-colors ${
                      isSelected ? "bg-brand-primary text-white" : "border border-surface-border"
                    }`}
                  >
                    {isSelected && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

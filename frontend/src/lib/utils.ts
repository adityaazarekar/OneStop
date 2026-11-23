export function formatINR(amount: number | null | undefined): string {
  if (amount == null || isNaN(amount)) return "₹0";
  return (
    "₹" +
    Number(amount).toLocaleString("en-IN", {
      maximumFractionDigits: 0,
    })
  );
}

export function retailerLabel(retailer: string): string {
  const map: Record<string, string> = {
    amazon_in: "Amazon.in",
    flipkart: "Flipkart",
    croma: "Croma",
    reliance_digital: "Reliance Digital",
    vijaysales: "Vijay Sales",
    tatacliq: "Tata CLiQ",
    jiomart: "JioMart",
  };
  return map[retailer] || retailer.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function trustColorClass(score: number): {
  bg: string;
  text: string;
  border: string;
  label: string;
} {
  if (score >= 75) {
    return {
      bg: "bg-emerald-500/10",
      text: "text-emerald-400",
      border: "border-emerald-500/30",
      label: "Trusted / Low Risk",
    };
  }
  if (score >= 45) {
    return {
      bg: "bg-amber-500/10",
      text: "text-amber-400",
      border: "border-amber-500/30",
      label: "Moderate Risk",
    };
  }
  return {
    bg: "bg-rose-500/10",
    text: "text-rose-400",
    border: "border-rose-500/30",
    label: "High Scam Risk",
  };
}

export function verdictBadge(verdict: string): {
  bg: string;
  text: string;
  border: string;
  emoji: string;
} {
  switch (verdict) {
    case "BUY_NOW":
      return { bg: "bg-emerald-500/15", text: "text-emerald-300", border: "border-emerald-500/40", emoji: "🟢" };
    case "WAIT":
      return { bg: "bg-amber-500/15", text: "text-amber-300", border: "border-amber-500/40", emoji: "🟡" };
    case "OVERPRICED":
      return { bg: "bg-rose-500/15", text: "text-rose-300", border: "border-rose-500/40", emoji: "🔴" };
    case "FAIR_PRICE":
      return { bg: "bg-indigo-500/15", text: "text-indigo-300", border: "border-indigo-500/40", emoji: "🔵" };
    default:
      return { bg: "bg-slate-500/15", text: "text-slate-300", border: "border-slate-500/40", emoji: "⚪" };
  }
}

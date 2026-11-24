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
  return (
    map[retailer] ||
    retailer.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

export type SemanticTone = "accent" | "warn" | "danger" | "neutral";

export function trustTone(score: number): {
  tone: SemanticTone;
  label: string;
} {
  if (score >= 75) return { tone: "accent", label: "Good" };
  if (score >= 45) return { tone: "warn", label: "Moderate" };
  return { tone: "danger", label: "Caution" };
}

export function verdictInfo(verdict: string): {
  tone: SemanticTone;
  label: string;
} {
  switch (verdict) {
    case "BUY_NOW":
      return { tone: "accent", label: "Buy now" };
    case "WAIT":
      return { tone: "warn", label: "Wait" };
    case "OVERPRICED":
      return { tone: "danger", label: "Overpriced" };
    case "FAIR_PRICE":
      return { tone: "neutral", label: "Fair price" };
    case "INSUFFICIENT_HISTORY":
      return { tone: "neutral", label: "Fair price" };
    default:
      return { tone: "neutral", label: verdict.replace(/_/g, " ") };
  }
}

import {
  AnalysisResponse,
  CreditCard,
  ExchangeComparisonResponse,
  PriceHistoryResponse,
  ProductDetails,
  RecentProduct,
  VaultItem,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api/v1";

export async function fetchCards(): Promise<CreditCard[]> {
  try {
    const res = await fetch(`${API_BASE}/cards`, { cache: "no-store" });
    if (!res.ok) throw new Error("Failed to load cards");
    return res.json();
  } catch (err) {
    console.warn("Could not fetch cards:", err);
    return [];
  }
}

export async function fetchRecentProducts(limit = 12): Promise<RecentProduct[]> {
  try {
    const res = await fetch(`${API_BASE}/products/recent?limit=${limit}`, {
      cache: "no-store",
    });
    if (!res.ok) throw new Error("Failed to load recent products");
    return res.json();
  } catch (err) {
    console.warn("Could not fetch recent products:", err);
    return [];
  }
}

export async function fetchProductDetails(id: string): Promise<ProductDetails | null> {
  try {
    const res = await fetch(`${API_BASE}/products/${id}`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch (err) {
    console.warn(`Could not fetch product ${id}:`, err);
    return null;
  }
}

export async function fetchProductHistory(
  id: string,
  days = 90
): Promise<PriceHistoryResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/products/${id}/history?days=${days}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json();
  } catch (err) {
    console.warn(`Could not fetch history for ${id}:`, err);
    return null;
  }
}

export async function analyzeUrl(
  url: string,
  cardIds: string[] = []
): Promise<AnalysisResponse> {
  const queryParams = cardIds.map((id) => `card_ids=${encodeURIComponent(id)}`).join("&");
  const fullUrl = `${API_BASE}/analyze-url?url=${encodeURIComponent(url)}${
    queryParams ? `&${queryParams}` : ""
  }`;

  const res = await fetch(fullUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(
      errorData.detail ||
        "Live fetch failed. The retailer may block server IPs. Please use the OneStop Chrome Extension."
    );
  }
  return res.json();
}

export async function recalculateEffectivePrice(
  listedPrice: number,
  cardIds: string[],
  retailer = "amazon_in",
  couponDiscount = 0,
  platformFee = 0,
  bankOffers: any[] = []
) {
  const res = await fetch(`${API_BASE}/calculate-effective-price`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      listed_price: listedPrice,
      card_ids: cardIds,
      retailer,
      coupon_discount: couponDiscount,
      platform_fee: platformFee,
      bank_offers: bankOffers,
    }),
  });
  if (!res.ok) throw new Error("Failed to calculate effective price");
  return res.json();
}

export async function compareExchange(
  newDevicePrice: number,
  marketplaceQuote: number,
  externalQuote: number
): Promise<ExchangeComparisonResponse> {
  const res = await fetch(
    `${API_BASE}/compare-exchange?new_device_price=${newDevicePrice}&marketplace_exchange_quote=${marketplaceQuote}&external_buyback_quote=${externalQuote}`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error("Failed to compare exchange options");
  return res.json();
}

export async function uploadInvoice(
  file: File,
  userEmail: string
): Promise<VaultItem> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(
    `${API_BASE}/vault/upload-invoice?user_email=${encodeURIComponent(userEmail)}`,
    {
      method: "POST",
      body: formData,
    }
  );

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || "Invoice upload failed");
  }
  return res.json();
}

export async function fetchVaultItems(userEmail: string): Promise<VaultItem[]> {
  try {
    const res = await fetch(
      `${API_BASE}/vault/items?user_email=${encodeURIComponent(userEmail)}`,
      { cache: "no-store" }
    );
    if (!res.ok) return [];
    return res.json();
  } catch (err) {
    console.warn("Could not fetch vault items:", err);
    return [];
  }
}

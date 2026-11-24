"""
Side-by-side VariantSpec Labeling CLI & Golden Dataset Builder (`scripts/label.py`).

Usage:
  - Generate/refresh the >=60-product (200+ pair) golden eval set across electronics,
    fashion, grocery, and appliances (including all M3 Additions hard-negative & collision slices):
      python scripts/label.py --build-golden
  - Inspect side-by-side specs from golden.jsonl:
      python scripts/label.py --inspect --limit 5
  - Interactive CLI labeling mode:
      python scripts/label.py --interactive <input.jsonl> --output <output.jsonl>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.matching.contracts import Attr, AttrKind, Bucket, Evidence, Source, VariantSpec, Verdict
from app.matching.normalize import (
    normalize_color,
    normalize_condition,
    normalize_quantity,
    normalize_size,
    parse_pack_and_quantity,
    parse_ram_and_storage,
)

GOLDEN_PATH = BACKEND_DIR / "tests" / "eval" / "golden.jsonl"


def _mk_attr(
    name: str,
    kind: AttrKind,
    raw: str,
    source: Source = Source.DOM_SELECTED,
    span: str | None = None,
    conf: float = 0.98,
) -> dict[str, Any]:
    ev = Evidence(source=source, span=span if span is not None else raw, confidence=conf)
    if kind == AttrKind.QUANTITY:
        num, unit, norm = normalize_quantity(raw, name)
        return Attr(name=name, kind=kind, raw=raw, norm=norm, num=num, unit=unit, evidence=ev).model_dump(mode="json")
    if kind == AttrKind.COUNT:
        import re
        from decimal import Decimal
        m = re.search(r"(\d+)", raw)
        num = Decimal(m.group(1)) if m else None
        norm = str(int(num)) if num is not None else None
        return Attr(name=name, kind=kind, raw=raw, norm=norm, num=num, unit="count", evidence=ev).model_dump(mode="json")
    if kind == AttrKind.COLOR:
        canon, _ = normalize_color(raw)
        return Attr(name=name, kind=kind, raw=raw, norm=canon, evidence=ev).model_dump(mode="json")
    if kind == AttrKind.SIZE:
        sys_code, num, norm = normalize_size(raw)
        return Attr(name=name, kind=kind, raw=raw, norm=norm, num=num, unit=sys_code, evidence=ev).model_dump(mode="json")
    if kind == AttrKind.ENUM:
        norm = normalize_condition(raw) if name == "condition" else raw.strip().casefold()
        return Attr(name=name, kind=kind, raw=raw, norm=norm, evidence=ev).model_dump(mode="json")
    return Attr(name=name, kind=kind, raw=raw, norm=raw.strip().upper(), evidence=ev).model_dump(mode="json")


def _build_64_base_products() -> list[dict[str, Any]]:
    """
    64 distinct canonical Indian products spanning:
      - Electronics (20 products: smartphones, laptops, tablets, audio, wearables, peripherals)
      - Fashion & Footwear (16 products: running shoes, sneakers, t-shirts, jeans, kurtas, jackets)
      - Grocery & Personal Care (14 products: cleansers, serums, atta, oils, tea, protein, diapers)
      - Home & Kitchen Appliances (14 products: pressure cookers, ACs, air fryers, mixers, TVs, purifiers)
    """
    products: list[dict[str, Any]] = [
        # ── Electronics (1..20) ──
        {"pid": "E01", "cat": "electronics", "brand": "Apple", "model": "iPhone 15", "ptype": "smartphone", "gtin": "0195949036057", "price": 69999, "attrs": {"color": ("COLOR", "Black"), "storage": ("QUANTITY", "128GB"), "condition": ("ENUM", "New")}, "flip_attr": ("storage", "QUANTITY", "256GB"), "alt_model": "iPhone 15 Pro"},
        {"pid": "E02", "cat": "electronics", "brand": "Apple", "model": "iPhone 16 Pro", "ptype": "smartphone", "gtin": "0195949722141", "price": 119900, "attrs": {"color": ("COLOR", "Black"), "storage": ("QUANTITY", "256GB"), "condition": ("ENUM", "New")}, "flip_attr": ("storage", "QUANTITY", "512GB"), "alt_model": "iPhone 16 Pro Max"},
        {"pid": "E03", "cat": "electronics", "brand": "Samsung", "model": "Galaxy S24 5G", "ptype": "smartphone", "gtin": "8806095373614", "price": 64999, "attrs": {"color": ("COLOR", "Black"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "256GB"), "condition": ("ENUM", "New")}, "flip_attr": ("storage", "QUANTITY", "128GB"), "alt_model": "Galaxy S24 Ultra"},
        {"pid": "E04", "cat": "electronics", "brand": "Samsung", "model": "Galaxy S24 Ultra", "ptype": "smartphone", "gtin": "8806095411224", "price": 121999, "attrs": {"color": ("COLOR", "Gray"), "ram": ("QUANTITY", "12GB"), "storage": ("QUANTITY", "512GB")}, "flip_attr": ("color", "COLOR", "Black"), "alt_model": "Galaxy S24 Plus"},
        {"pid": "E05", "cat": "electronics", "brand": "OnePlus", "model": "12 5G", "ptype": "smartphone", "gtin": "6921815625711", "price": 64999, "attrs": {"color": ("COLOR", "Green"), "ram": ("QUANTITY", "12GB"), "storage": ("QUANTITY", "256GB")}, "flip_attr": ("ram", "QUANTITY", "16GB"), "alt_model": "12R 5G"},
        {"pid": "E06", "cat": "electronics", "brand": "Google", "model": "Pixel 8a", "ptype": "smartphone", "gtin": "0840244708115", "price": 49999, "attrs": {"color": ("COLOR", "Blue"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "128GB")}, "flip_attr": ("color", "COLOR", "Black"), "alt_model": "Pixel 8 Pro"},
        {"pid": "E07", "cat": "electronics", "brand": "Xiaomi", "model": "14 Civi", "ptype": "smartphone", "gtin": "6941812771129", "price": 42999, "attrs": {"color": ("COLOR", "Blue"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "256GB")}, "flip_attr": ("storage", "QUANTITY", "512GB"), "alt_model": "14 Ultra"},
        {"pid": "E08", "cat": "electronics", "brand": "Nothing", "model": "Phone 2a", "ptype": "smartphone", "gtin": "5060928371449", "price": 23999, "attrs": {"color": ("COLOR", "White"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "128GB")}, "flip_attr": ("color", "COLOR", "Black"), "alt_model": "Phone 2a Plus"},
        {"pid": "E09", "cat": "electronics", "brand": "Apple", "model": "MacBook Air M2", "ptype": "laptop", "gtin": "0194253081234", "price": 74990, "attrs": {"color": ("COLOR", "Silver"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "256GB")}, "flip_attr": ("storage", "QUANTITY", "512GB"), "alt_model": "MacBook Pro M2"},
        {"pid": "E10", "cat": "electronics", "brand": "Apple", "model": "MacBook Pro M3", "ptype": "laptop", "gtin": "0195949112232", "price": 169900, "attrs": {"color": ("COLOR", "Silver"), "ram": ("QUANTITY", "16GB"), "storage": ("QUANTITY", "1TB")}, "flip_attr": ("ram", "QUANTITY", "8GB"), "alt_model": "MacBook Air M3"},
        {"pid": "E11", "cat": "electronics", "brand": "ASUS", "model": "ROG Zephyrus G14", "ptype": "laptop", "gtin": "4711387481127", "price": 149990, "attrs": {"color": ("COLOR", "White"), "ram": ("QUANTITY", "16GB"), "storage": ("QUANTITY", "1TB")}, "flip_attr": ("color", "COLOR", "Gray"), "alt_model": "ROG Zephyrus G16"},
        {"pid": "E12", "cat": "electronics", "brand": "Lenovo", "model": "ThinkPad E14 Gen 5", "ptype": "laptop", "gtin": "0197529441121", "price": 68990, "attrs": {"color": ("COLOR", "Black"), "ram": ("QUANTITY", "16GB"), "storage": ("QUANTITY", "512GB"), "condition": ("ENUM", "New")}, "flip_attr": ("condition", "ENUM", "Refurbished"), "alt_model": "ThinkPad L14 Gen 5"},
        {"pid": "E13", "cat": "electronics", "brand": "Sony", "model": "WH-1000XM5", "ptype": "headphones", "gtin": "4548736132580", "price": 26990, "attrs": {"color": ("COLOR", "Black"), "condition": ("ENUM", "New")}, "flip_attr": ("color", "COLOR", "Silver"), "alt_model": "WH-1000XM4"},
        {"pid": "E14", "cat": "electronics", "brand": "Bose", "model": "QuietComfort Ultra Headphones", "ptype": "headphones", "gtin": "0017817846110", "price": 35900, "attrs": {"color": ("COLOR", "Black")}, "flip_attr": ("color", "COLOR", "White"), "alt_model": "QuietComfort Ultra Earbuds"},
        {"pid": "E15", "cat": "electronics", "brand": "Apple", "model": "iPad Air M2", "ptype": "tablet", "gtin": "0195949644122", "price": 59900, "attrs": {"color": ("COLOR", "Blue"), "storage": ("QUANTITY", "128GB"), "connectivity": ("ENUM", "wi-fi")}, "flip_attr": ("connectivity", "ENUM", "wi-fi + cellular"), "alt_model": "iPad Pro M4"},
        {"pid": "E16", "cat": "electronics", "brand": "Samsung", "model": "Galaxy Tab S9", "ptype": "tablet", "gtin": "8806095119328", "price": 72999, "attrs": {"color": ("COLOR", "Gray"), "ram": ("QUANTITY", "8GB"), "storage": ("QUANTITY", "128GB")}, "flip_attr": ("storage", "QUANTITY", "256GB"), "alt_model": "Galaxy Tab S9 FE"},
        {"pid": "E17", "cat": "electronics", "brand": "Keychron", "model": "K2 Wireless Mechanical Keyboard", "ptype": "keyboard", "gtin": "4895248811220", "price": 8499, "attrs": {"color": ("COLOR", "Black"), "switch": ("ENUM", "brown")}, "flip_attr": ("switch", "ENUM", "red"), "alt_model": "K8 Wireless Mechanical Keyboard"},
        {"pid": "E18", "cat": "electronics", "brand": "Logitech", "model": "MX Master 3S", "ptype": "mouse", "gtin": "0097855174321", "price": 8995, "attrs": {"color": ("COLOR", "Black")}, "flip_attr": ("color", "COLOR", "White"), "alt_model": "MX Anywhere 3S"},
        {"pid": "E19", "cat": "electronics", "brand": "SanDisk", "model": "Extreme Portable SSD E61", "ptype": "ssd", "gtin": "0619659181123", "price": 9499, "attrs": {"color": ("COLOR", "Black"), "storage": ("QUANTITY", "1TB")}, "flip_attr": ("storage", "QUANTITY", "2TB"), "alt_model": "Extreme Pro Portable SSD E81"},
        {"pid": "E20", "cat": "electronics", "brand": "Garmin", "model": "Forerunner 265", "ptype": "smartwatch", "gtin": "0753759313227", "price": 49490, "attrs": {"color": ("COLOR", "Black")}, "flip_attr": ("color", "COLOR", "Blue"), "alt_model": "Forerunner 265S"},

        # ── Fashion & Footwear (21..36) ──
        {"pid": "F01", "cat": "fashion", "brand": "Nike", "model": "Air Zoom Pegasus 40", "ptype": "shoes", "gtin": "0196604123458", "price": 10495, "attrs": {"color": ("COLOR", "Black"), "size": ("SIZE", "UK 9")}, "flip_attr": ("size", "SIZE", "US 9"), "alt_model": "Air Zoom Pegasus 39"},
        {"pid": "F02", "cat": "fashion", "brand": "Adidas", "model": "Ultraboost Light", "ptype": "shoes", "gtin": "4066751234119", "price": 15999, "attrs": {"color": ("COLOR", "White"), "size": ("SIZE", "UK 10")}, "flip_attr": ("size", "SIZE", "UK 9"), "alt_model": "Ultraboost 22"},
        {"pid": "F03", "cat": "fashion", "brand": "Puma", "model": "Velocity Nitro 3", "ptype": "shoes", "gtin": "4099686111229", "price": 9999, "attrs": {"color": ("COLOR", "Blue"), "size": ("SIZE", "UK 8")}, "flip_attr": ("color", "COLOR", "Red"), "alt_model": "Deviate Nitro 3"},
        {"pid": "F04", "cat": "fashion", "brand": "ASICS", "model": "Gel-Kayano 30", "ptype": "shoes", "gtin": "4550456778122", "price": 14999, "attrs": {"color": ("COLOR", "Black"), "size": ("SIZE", "UK 9")}, "flip_attr": ("size", "SIZE", "EU 42"), "alt_model": "Gel-Nimbus 26"},
        {"pid": "F05", "cat": "fashion", "brand": "New Balance", "model": "Fresh Foam X 1080v13", "ptype": "shoes", "gtin": "0196941882114", "price": 15499, "attrs": {"color": ("COLOR", "Gray"), "size": ("SIZE", "UK 9.5")}, "flip_attr": ("size", "SIZE", "UK 10.5"), "alt_model": "Fresh Foam X 880v13"},
        {"pid": "F06", "cat": "fashion", "brand": "Crocs", "model": "Classic Clog", "ptype": "clogs", "gtin": "0887350122114", "price": 3495, "attrs": {"color": ("COLOR", "Navy"), "size": ("SIZE", "UK 8")}, "flip_attr": ("color", "COLOR", "Green"), "alt_model": "Baya Clog"},
        {"pid": "F07", "cat": "fashion", "brand": "Skechers", "model": "Go Walk 6", "ptype": "shoes", "gtin": "0195969331214", "price": 6499, "attrs": {"color": ("COLOR", "Black"), "size": ("SIZE", "UK 9")}, "flip_attr": ("size", "SIZE", "UK 8"), "alt_model": "Go Walk 7"},
        {"pid": "F08", "cat": "fashion", "brand": "Levi's", "model": "511 Slim Crew Tee", "ptype": "t-shirt", "gtin": "5400891223110", "price": 1199, "attrs": {"color": ("COLOR", "Navy"), "size": ("SIZE", "M")}, "flip_attr": ("size", "SIZE", "L"), "alt_model": "501 Original Crew Tee"},
        {"pid": "F09", "cat": "fashion", "brand": "Levi's", "model": "512 Slim Tapered Jeans", "ptype": "jeans", "gtin": "5400891445116", "price": 2899, "attrs": {"color": ("COLOR", "Blue"), "size": ("SIZE", "L")}, "flip_attr": ("color", "COLOR", "Black"), "alt_model": "511 Slim Fit Jeans"},
        {"pid": "F10", "cat": "fashion", "brand": "Allen Solly", "model": "Pique Polo T-Shirt", "ptype": "t-shirt", "gtin": "8907259112233", "price": 1099, "attrs": {"color": ("COLOR", "White"), "size": ("SIZE", "XL")}, "flip_attr": ("size", "SIZE", "M"), "alt_model": "Printed Crew T-Shirt"},
        {"pid": "F11", "cat": "fashion", "brand": "Van Heusen", "model": "Athleisure Smart Tech Polo", "ptype": "t-shirt", "gtin": "8907259881122", "price": 1299, "attrs": {"color": ("COLOR", "Olive"), "size": ("SIZE", "L"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("pack_qty", "COUNT", "2"), "alt_model": "Formal Cotton Shirt"},
        {"pid": "F12", "cat": "fashion", "brand": "US Polo Assn", "model": "Cotton Crew Neck Vest", "ptype": "vest", "gtin": "8905112334118", "price": 699, "attrs": {"color": ("COLOR", "White"), "size": ("SIZE", "M"), "pack_qty": ("COUNT", "Pack of 2")}, "flip_attr": ("pack_qty", "COUNT", "Pack of 3"), "alt_model": "Ribbed Cotton Briefs"},
        {"pid": "F13", "cat": "fashion", "brand": "Uniqlo", "model": "AIRism Cotton Oversized Crew Tee", "ptype": "t-shirt", "gtin": "4550344112231", "price": 1490, "attrs": {"color": ("COLOR", "Black"), "size": ("SIZE", "L")}, "flip_attr": ("color", "COLOR", "Purple"), "alt_model": "Supima Cotton Crew Tee"},
        {"pid": "F14", "cat": "fashion", "brand": "Fabindia", "model": "Cotton Chikankari Kurta", "ptype": "kurta", "gtin": "8904112339812", "price": 2499, "attrs": {"color": ("COLOR", "Yellow"), "size": ("SIZE", "M")}, "flip_attr": ("size", "SIZE", "XL"), "alt_model": "Silk Blend Nehru Jacket"},
        {"pid": "F15", "cat": "fashion", "brand": "Woodland", "model": "Camel Outdoor Trek Boots", "ptype": "boots", "gtin": "8903112994121", "price": 4295, "attrs": {"color": ("COLOR", "Brown"), "size": ("SIZE", "UK 9")}, "flip_attr": ("size", "SIZE", "US 9"), "alt_model": "Leather Casual Loafers"},
        {"pid": "F16", "cat": "fashion", "brand": "Ray-Ban", "model": "Aviator Classic RB3025", "ptype": "sunglasses", "gtin": "8052896020570", "price": 8590, "attrs": {"color": ("COLOR", "Gold"), "size": ("SIZE", "M")}, "flip_attr": ("color", "COLOR", "Silver"), "alt_model": "Wayfarer Classic RB2140"},

        # ── Grocery & Personal Care (37..50) ──
        {"pid": "G01", "cat": "grocery", "brand": "Cetaphil", "model": "Gentle Skin Cleanser", "ptype": "cleanser", "gtin": "3499320012311", "price": 1100, "attrs": {"unit_size": ("QUANTITY", "1 L"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "500 ml"), "alt_model": "Oily Skin Cleanser", "pack_trap": ("2 x 500 ml", 1200)},
        {"pid": "G02", "cat": "grocery", "brand": "Minimalist", "model": "10% Niacinamide Face Serum", "ptype": "serum", "gtin": "8906128100112", "price": 599, "attrs": {"unit_size": ("QUANTITY", "30 ml"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "60 ml"), "alt_model": "2% Salicylic Acid Face Serum", "pack_trap": ("2 x 15 ml", 649)},
        {"pid": "G03", "cat": "grocery", "brand": "CeraVe", "model": "Moisturizing Cream", "ptype": "cream", "gtin": "3337875597227", "price": 1450, "attrs": {"unit_size": ("QUANTITY", "454 g"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "340 g"), "alt_model": "Foaming Facial Cleanser"},
        {"pid": "G04", "cat": "grocery", "brand": "Nivea", "model": "Soft Light Moisturizer", "ptype": "cream", "gtin": "4005808812119", "price": 450, "attrs": {"unit_size": ("QUANTITY", "300 ml"), "pack_qty": ("COUNT", "Pack of 2")}, "flip_attr": ("pack_qty", "COUNT", "Pack of 3"), "alt_model": "Cocoa Nourish Body Lotion"},
        {"pid": "G05", "cat": "grocery", "brand": "Bioderma", "model": "Sensibio H2O Micellar Water", "ptype": "cleanser", "gtin": "3401395376812", "price": 999, "attrs": {"unit_size": ("QUANTITY", "500 ml"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "250 ml"), "alt_model": "Sebium H2O Micellar Water", "pack_trap": ("2 x 250 ml", 1050)},
        {"pid": "G06", "cat": "grocery", "brand": "Neutrogena", "model": "Hydro Boost Water Gel", "ptype": "moisturizer", "gtin": "4891080621445", "price": 950, "attrs": {"unit_size": ("QUANTITY", "50 g"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "15 g"), "alt_model": "Ultra Sheer Dry Touch Sunscreen"},
        {"pid": "G07", "cat": "grocery", "brand": "Aashirvaad", "model": "Shudh Chakki Whole Wheat Atta", "ptype": "atta", "gtin": "8901725121440", "price": 265, "attrs": {"unit_size": ("QUANTITY", "5 kg"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "10 kg"), "alt_model": "Select Sharbati Atta", "hinglish_title": "Aashirvaad Shudh Chakki Whole Wheat Atta (Gehu ka Aata) 5000 g"},
        {"pid": "G08", "cat": "grocery", "brand": "Tata Sampann", "model": "Unpolished Toor Dal", "ptype": "dal", "gtin": "8904043901112", "price": 195, "attrs": {"unit_size": ("QUANTITY", "1 kg"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "500 g"), "alt_model": "Unpolished Moong Dal", "pack_trap": ("2 x 500 g", 205), "hinglish_title": "Tata Sampann Unpolished Toor / Arhar Dal 1000 g"},
        {"pid": "G09", "cat": "grocery", "brand": "Tata Tea", "model": "Gold Assam Tea Leaves", "ptype": "tea", "gtin": "8901052011229", "price": 540, "attrs": {"unit_size": ("QUANTITY", "1 kg"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "500 g"), "alt_model": "Chakra Gold Tea", "pack_trap": ("2 x 500 g", 560)},
        {"pid": "G10", "cat": "grocery", "brand": "Fortune", "model": "Sunlite Refined Sunflower Oil", "ptype": "oil", "gtin": "8906007281115", "price": 145, "attrs": {"unit_size": ("QUANTITY", "1 L"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "5 L"), "alt_model": "Kachi Ghani Mustard Oil"},
        {"pid": "G11", "cat": "grocery", "brand": "Optimum Nutrition", "model": "Gold Standard 100% Whey Protein", "ptype": "protein", "gtin": "0748927028669", "price": 6899, "attrs": {"unit_size": ("QUANTITY", "2 kg"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "1 kg"), "alt_model": "Serious Mass Gainer"},
        {"pid": "G12", "cat": "grocery", "brand": "Davidoff", "model": "Rich Aroma Instant Coffee", "ptype": "coffee", "gtin": "4006067084119", "price": 599, "attrs": {"unit_size": ("QUANTITY", "100 g"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("pack_qty", "COUNT", "2"), "alt_model": "Fine Aroma Instant Coffee"},
        {"pid": "G13", "cat": "grocery", "brand": "Pampers", "model": "All Round Protection Pants", "ptype": "diapers", "gtin": "4987176123110", "price": 1099, "attrs": {"size": ("SIZE", "L"), "pack_qty": ("COUNT", "64")}, "flip_attr": ("size", "SIZE", "XL"), "alt_model": "Premium Care Pants"},
        {"pid": "G14", "cat": "grocery", "brand": "Mamaearth", "model": "Onion Hair Fall Control Shampoo", "ptype": "shampoo", "gtin": "8906087771223", "price": 499, "attrs": {"unit_size": ("QUANTITY", "400 ml"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "250 ml"), "alt_model": "Rosemary Anti Hair Fall Shampoo"},

        # ── Home & Kitchen Appliances (51..64) ──
        {"pid": "A01", "cat": "appliance", "brand": "Prestige", "model": "Svachh Popular Aluminium Pressure Cooker", "ptype": "cooker", "gtin": "8901365114228", "price": 1799, "attrs": {"unit_size": ("QUANTITY", "5 L"), "color": ("COLOR", "Silver")}, "flip_attr": ("unit_size", "QUANTITY", "3 L"), "alt_model": "Deluxe Alpha Stainless Steel Cooker"},
        {"pid": "A02", "cat": "appliance", "brand": "Hawkins", "model": "Contura Black Hard Anodised Pressure Cooker", "ptype": "cooker", "gtin": "8901165002116", "price": 2350, "attrs": {"unit_size": ("QUANTITY", "3 L"), "color": ("COLOR", "Black")}, "flip_attr": ("unit_size", "QUANTITY", "5 L"), "alt_model": "Classic Aluminium Pressure Cooker"},
        {"pid": "A03", "cat": "appliance", "brand": "Philips", "model": "HD9252/90 Digital Air Fryer", "ptype": "air fryer", "gtin": "8710103951112", "price": 7999, "attrs": {"unit_size": ("QUANTITY", "4.1 L"), "color": ("COLOR", "Black")}, "flip_attr": ("unit_size", "QUANTITY", "6.2 L"), "alt_model": "NA120/00 Analog Air Fryer"},
        {"pid": "A04", "cat": "appliance", "brand": "Dyson", "model": "V12 Detect Slim Absolute", "ptype": "vacuum", "gtin": "5025155071113", "price": 52900, "attrs": {"color": ("COLOR", "Yellow"), "condition": ("ENUM", "New")}, "flip_attr": ("condition", "ENUM", "Refurbished"), "alt_model": "V15 Detect Absolute"},
        {"pid": "A05", "cat": "appliance", "brand": "LG", "model": "Dual Inverter Split AC TS-Q19YNZE", "ptype": "ac", "gtin": "8806091882112", "price": 44990, "attrs": {"color": ("COLOR", "White")}, "flip_attr": ("color", "COLOR", "Silver"), "alt_model": "Dual Inverter Split AC TS-Q18JNXE"},
        {"pid": "A06", "cat": "appliance", "brand": "Daikin", "model": "3 Star Inverter Split AC MTKL50U", "ptype": "ac", "gtin": "4548848881215", "price": 36990, "attrs": {"color": ("COLOR", "White")}, "flip_attr": ("color", "COLOR", "Gray"), "alt_model": "5 Star Inverter Split AC MTKM50U"},
        {"pid": "A07", "cat": "appliance", "brand": "Bosch", "model": "Serie 4 Front Load Washing Machine WAJ2416SIN", "ptype": "washer", "gtin": "4242005221110", "price": 31990, "attrs": {"unit_size": ("QUANTITY", "7 kg"), "color": ("COLOR", "Silver")}, "flip_attr": ("unit_size", "QUANTITY", "8 kg"), "alt_model": "Serie 6 Front Load Washing Machine"},
        {"pid": "A08", "cat": "appliance", "brand": "Samsung", "model": "EcoBubble Top Load Washing Machine", "ptype": "washer", "gtin": "8806094991112", "price": 19490, "attrs": {"unit_size": ("QUANTITY", "7 kg"), "color": ("COLOR", "Gray")}, "flip_attr": ("unit_size", "QUANTITY", "8 kg"), "alt_model": "AI EcoBubble Front Load Washing Machine"},
        {"pid": "A09", "cat": "appliance", "brand": "Atomberg", "model": "Renesa BLDC Ceiling Fan", "ptype": "fan", "gtin": "8908006512114", "price": 3599, "attrs": {"unit_size": ("QUANTITY", "1200 mm"), "color": ("COLOR", "White"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("color", "COLOR", "Brown"), "alt_model": "Efficio Alpha BLDC Ceiling Fan"},
        {"pid": "A10", "cat": "appliance", "brand": "Kent", "model": "Supreme RO Water Purifier", "ptype": "purifier", "gtin": "8906078112214", "price": 14499, "attrs": {"unit_size": ("QUANTITY", "8 L"), "color": ("COLOR", "White")}, "flip_attr": ("unit_size", "QUANTITY", "9 L"), "alt_model": "Grand Plus RO Water Purifier"},
        {"pid": "A11", "cat": "appliance", "brand": "Sujata", "model": "Dynamix DX Mixer Grinder", "ptype": "mixer", "gtin": "8906019881122", "price": 6190, "attrs": {"color": ("COLOR", "White"), "pack_qty": ("COUNT", "3")}, "flip_attr": ("pack_qty", "COUNT", "2"), "alt_model": "Powermatic Plus Juicer Mixer Grinder"},
        {"pid": "A12", "cat": "appliance", "brand": "Crompton", "model": "Arno Neo Storage Water Heater", "ptype": "geyser", "gtin": "8902653221114", "price": 6299, "attrs": {"unit_size": ("QUANTITY", "15 L"), "color": ("COLOR", "White")}, "flip_attr": ("unit_size", "QUANTITY", "25 L"), "alt_model": "Rapid Jet Instant Water Heater"},
        {"pid": "A13", "cat": "appliance", "brand": "Milton", "model": "Thermosteel Flip Lid Flask", "ptype": "flask", "gtin": "8902541008112", "price": 999, "attrs": {"unit_size": ("QUANTITY", "1000 ml"), "color": ("COLOR", "Silver"), "pack_qty": ("COUNT", "1")}, "flip_attr": ("unit_size", "QUANTITY", "500 ml"), "alt_model": "Aura Thermosteel Bottle", "pack_trap": ("2 x 500 ml", 1099)},
        {"pid": "A14", "cat": "appliance", "brand": "Wakefit", "model": "Orthopedic Memory Foam Mattress", "ptype": "mattress", "gtin": "8906114229110", "price": 12499, "attrs": {"color": ("COLOR", "White"), "size": ("SIZE", "L")}, "flip_attr": ("size", "SIZE", "XL"), "alt_model": "Dual Comfort Mattress"},
    ]
    return products


def build_golden_records() -> list[dict[str, Any]]:
    """
    Build >=60 products x multiple candidates (205 labeled pairs total) covering:
      - `exact_positive` (64 pairs)
      - `single_attr_flip` (64 pairs: same model, one of color/size/pack/storage/condition flipped -> VARIANT)
      - `missing_attr_positive` (32 pairs: same model, candidate omits color/size/storage -> UNVERIFIED)
      - `hard_model_reject` (24 pairs: same brand, adjacent model tier/generation -> REJECTED)
      - `pack_size_trap` (6 pairs: e.g. 2 x 500 ml vs 1 L -> VARIANT with unit price)
      - `hinglish_transliterated` (8 pairs: Indian Hinglish/unit-equivalent titles -> EXACT)
      - `gtin_collision` (8 pairs: identical GTIN barcode with conflicting attribute or product name -> UNVERIFIED + gtin_collision_suspect)
    """
    base_products = _build_64_base_products()
    records: list[dict[str, Any]] = []

    for idx, p in enumerate(base_products):
        pid = p["pid"]
        cat = p["cat"]
        brand = p["brand"]
        model = p["model"]
        ptype = p["ptype"]
        gtin = p["gtin"]
        price = p["price"]
        raw_attrs: dict[str, tuple[str, str]] = p["attrs"]

        src_attrs: dict[str, Any] = {}
        attr_desc_parts: list[str] = []
        for k, (k_kind, k_raw) in raw_attrs.items():
            src_attrs[k] = _mk_attr(k, AttrKind[k_kind], k_raw)
            attr_desc_parts.append(k_raw)

        src_title = f"{brand} {model} ({', '.join(attr_desc_parts)})"
        src_spec = {
            "url": f"https://www.amazon.in/dp/B000000{pid}",
            "title": src_title,
            "brand": brand,
            "product_type": ptype,
            "core_model": model,
            "gtin": gtin,
            "mpn": f"MPN-{pid}",
            "attrs": src_attrs,
            "image_urls": [f"https://images.example.in/{pid}.jpg"],
            "listed_price": str(price),
        }

        # ── 1. Exact Positive Candidate (every product) ──
        cand_exact_attrs = {}
        per_attr_exact = {}
        for k, (k_kind, k_raw) in raw_attrs.items():
            # Use equivalent representation for storage (1TB -> 1000GB) or size (M -> Medium) on some exact pairs
            c_raw = k_raw
            if k == "storage" and k_raw == "1TB":
                c_raw = "1000GB"
            elif k == "size" and k_raw == "M":
                c_raw = "Medium"
            cand_exact_attrs[k] = _mk_attr(k, AttrKind[k_kind], c_raw, source=Source.JSONLD)
            per_attr_exact[k] = Verdict.MATCH.value

        records.append({
            "pair_id": f"{pid}_exact",
            "product_id": pid,
            "category": cat,
            "slice": "exact_positive",
            "source_spec": src_spec,
            "cand_spec": {
                "url": f"https://www.croma.com/p/{pid}-exact",
                "title": f"{brand} {model} {' '.join(attr_desc_parts)}",
                "brand": brand,
                "product_type": ptype,
                "core_model": model,
                "gtin": gtin,
                "mpn": f"MPN-{pid}",
                "attrs": cand_exact_attrs,
                "image_urls": [],
                "listed_price": str(int(price * 0.98)),
            },
            "expected_bucket": Bucket.EXACT.value,
            "per_attr_truth": per_attr_exact,
        })

        # ── 2. Single-Attribute-Flip Hard Negative (every product) ──
        flip_k, flip_kind, flip_raw = p["flip_attr"]
        cand_flip_attrs = dict(src_attrs)
        cand_flip_attrs[flip_k] = _mk_attr(flip_k, AttrKind[flip_kind], flip_raw, source=Source.DOM_SELECTED)
        per_attr_flip = {k: Verdict.MATCH.value for k in raw_attrs}
        per_attr_flip[flip_k] = Verdict.MISMATCH.value
        flip_title_parts = [flip_raw if k == flip_k else v[1] for k, v in raw_attrs.items()]
        # Omit GTIN on normal store candidate so it tests pure single-attribute flip->VARIANT
        records.append({
            "pair_id": f"{pid}_flip_{flip_k}",
            "product_id": pid,
            "category": cat,
            "slice": "single_attr_flip",
            "source_spec": {**src_spec, "gtin": None},
            "cand_spec": {
                "url": f"https://www.flipkart.com/p/{pid}-flip",
                "title": f"{brand} {model} ({', '.join(flip_title_parts)})",
                "brand": brand,
                "product_type": ptype,
                "core_model": model,
                "gtin": None,
                "mpn": None,
                "attrs": cand_flip_attrs,
                "image_urls": [],
                "listed_price": str(int(price * 0.92)),
            },
            "expected_bucket": Bucket.VARIANT.value,
            "per_attr_truth": per_attr_flip,
        })

        # ── 3. Missing-Attribute Positive -> UNVERIFIED (32 products) ──
        if idx % 2 == 0:
            drop_k = next(iter(raw_attrs.keys()))
            cand_missing_attrs = {k: v for k, v in src_attrs.items() if k != drop_k}
            per_attr_missing = {k: (Verdict.UNKNOWN.value if k == drop_k else Verdict.MATCH.value) for k in raw_attrs}
            rem_parts = [v[1] for k, v in raw_attrs.items() if k != drop_k]
            records.append({
                "pair_id": f"{pid}_missing_{drop_k}",
                "product_id": pid,
                "category": cat,
                "slice": "missing_attr_positive",
                "source_spec": {**src_spec, "gtin": None},
                "cand_spec": {
                    "url": f"https://www.vijaysales.com/p/{pid}-unconfirmed",
                    "title": f"{brand} {model} {' '.join(rem_parts)}".strip(),
                    "brand": brand,
                    "product_type": ptype,
                    "core_model": model,
                    "gtin": None,
                    "mpn": None,
                    "attrs": cand_missing_attrs,
                    "image_urls": [],
                    "listed_price": str(int(price * 0.95)),
                },
                "expected_bucket": Bucket.UNVERIFIED.value,
                "per_attr_truth": per_attr_missing,
            })

        # ── 4. Hard Model Reject (24 products) ──
        if idx < 24:
            alt_model = p["alt_model"]
            records.append({
                "pair_id": f"{pid}_reject_model",
                "product_id": pid,
                "category": cat,
                "slice": "hard_model_reject",
                "source_spec": {**src_spec, "gtin": None},
                "cand_spec": {
                    "url": f"https://www.reliancedigital.in/p/{pid}-alt",
                    "title": f"{brand} {alt_model} ({', '.join(attr_desc_parts)})",
                    "brand": brand,
                    "product_type": ptype,
                    "core_model": alt_model,
                    "gtin": None,
                    "mpn": None,
                    "attrs": dict(src_attrs),
                    "image_urls": [],
                    "listed_price": str(int(price * 1.15)),
                },
                "expected_bucket": Bucket.REJECTED.value,
                "per_attr_truth": {},
            })

        # ── 5. Pack-Size Trap ("2 x 500 ml" vs "1 L") ──
        if "pack_trap" in p:
            trap_expr, trap_price = p["pack_trap"]
            trap_attrs_parsed = {
                k: a.model_dump(mode="json")
                for k, a in parse_pack_and_quantity(trap_expr).items()
            }
            for k, v in src_attrs.items():
                if k not in ("pack_qty", "unit_size"):
                    trap_attrs_parsed[k] = v
            records.append({
                "pair_id": f"{pid}_pack_trap",
                "product_id": pid,
                "category": cat,
                "slice": "pack_size_trap",
                "source_spec": {**src_spec, "gtin": None},
                "cand_spec": {
                    "url": f"https://www.nykaa.com/p/{pid}-multipack",
                    "title": f"{brand} {model} ({trap_expr})",
                    "brand": brand,
                    "product_type": ptype,
                    "core_model": model,
                    "gtin": None,
                    "mpn": None,
                    "attrs": trap_attrs_parsed,
                    "image_urls": [],
                    "listed_price": str(trap_price),
                },
                "expected_bucket": Bucket.VARIANT.value,
                "per_attr_truth": {"pack_qty": Verdict.MISMATCH.value, "unit_size": Verdict.MISMATCH.value},
            })

        # ── 6. GTIN Collision Pairs (8 pairs: identical GTIN + conflicting attribute or different product name) ──
        if idx < 8:
            if idx % 2 == 0:
                # GTIN equal, attribute (color/storage) differs
                records.append({
                    "pair_id": f"{pid}_gtin_collision_attr",
                    "product_id": pid,
                    "category": cat,
                    "slice": "gtin_collision",
                    "source_spec": src_spec,
                    "cand_spec": {
                        "url": f"https://www.tatacliq.com/p/{pid}-gtin-attr-conflict",
                        "title": f"{brand} {model} ({', '.join(flip_title_parts)})",
                        "brand": brand,
                        "product_type": ptype,
                        "core_model": model,
                        "gtin": gtin,  # SAME GTIN!
                        "mpn": f"MPN-{pid}",
                        "attrs": cand_flip_attrs,
                        "image_urls": [],
                        "listed_price": str(price),
                    },
                    "expected_bucket": Bucket.UNVERIFIED.value,
                    "per_attr_truth": per_attr_flip,
                })
            else:
                # GTIN equal, completely mismatched product name/model (e.g. recycled barcode on accessory/other model)
                records.append({
                    "pair_id": f"{pid}_gtin_collision_name",
                    "product_id": pid,
                    "category": cat,
                    "slice": "gtin_collision",
                    "source_spec": src_spec,
                    "cand_spec": {
                        "url": f"https://www.jiomart.com/p/{pid}-gtin-hijack",
                        "title": f"{brand} {p['alt_model']} Silicone Case Cover",
                        "brand": brand,
                        "product_type": "case",
                        "core_model": f"{p['alt_model']} Silicone Case",
                        "gtin": gtin,  # SAME GTIN!
                        "mpn": None,
                        "attrs": dict(src_attrs),
                        "image_urls": [],
                        "listed_price": "499",
                    },
                    "expected_bucket": Bucket.UNVERIFIED.value,
                    "per_attr_truth": {},
                })

    # ── 7. Hinglish / Transliterated / Indian Unit-Equivalent Slice (8 pairs) ──
    hinglish_cases = [
        ("H01", "grocery", "Aashirvaad", "Shudh Chakki Atta", "atta", {"unit_size": ("QUANTITY", "5 kg")}, {"unit_size": ("QUANTITY", "5000 g")}, "Aashirvaad Shudh Chakki Atta (Gehu Aata) 5 kg", "Chakki Atta Shudh Aashirvaad 5000g"),
        ("H02", "grocery", "Tata Sampann", "Haldi Powder", "spice", {"unit_size": ("QUANTITY", "200 g")}, {"unit_size": ("QUANTITY", "0.2 kg")}, "Tata Sampann Haldi Powder (Turmeric) 200 g", "Haldi Powder Tata Sampann 200gm"),
        ("H03", "grocery", "Fortune", "Kachi Ghani Sarson Oil", "oil", {"unit_size": ("QUANTITY", "1 L")}, {"unit_size": ("QUANTITY", "1000 ml")}, "Fortune Kachi Ghani Sarson Oil 1 L", "Kachi Ghani Sarson Oil Fortune 1000 ml"),
        ("H04", "grocery", "MDH", "Deggi Mirch Lal Mirchi Powder", "spice", {"unit_size": ("QUANTITY", "100 g")}, {"unit_size": ("QUANTITY", "100 g")}, "MDH Deggi Mirch Lal Mirchi Powder 100g", "Lal Mirchi Powder Deggi Mirch MDH 100 gm"),
        ("H05", "appliance", "Prestige", "Svachh Inner Lid Cooker", "cooker", {"unit_size": ("QUANTITY", "5 L"), "color": ("COLOR", "Silver")}, {"unit_size": ("QUANTITY", "5000 ml"), "color": ("COLOR", "Silver")}, "Prestige Svachh Inner Lid Cooker 5 Litre Silver", "Inner Lid Cooker Svachh Prestige 5000 ml Silver"),
        ("H06", "fashion", "Manyavar", "Chikankari Kurta Pajama", "kurta", {"color": ("COLOR", "Yellow"), "size": ("SIZE", "L")}, {"color": ("COLOR", "Yellow"), "size": ("SIZE", "Large")}, "Manyavar Haldi Special Chikankari Kurta Pajama Size L", "Chikankari Kurta Pajama Manyavar Large Yellow"),
        ("H07", "electronics", "boAt", "Airdopes 141 TWS", "earbuds", {"color": ("COLOR", "Black")}, {"color": ("COLOR", "Black")}, "boAt Airdopes 141 TWS Earbuds (Black)", "Airdopes 141 TWS boAt Black"),
        ("H08", "grocery", "Amul", "Shudh Desi Cow Ghee", "ghee", {"unit_size": ("QUANTITY", "1 L")}, {"unit_size": ("QUANTITY", "1000 ml")}, "Amul Shudh Desi Cow Ghee 1 Litre Tin", "Desi Cow Ghee Shudh Amul 1000ml"),
    ]
    for hid, hcat, hbrand, hmodel, hptype, s_map, c_map, s_title, c_title in hinglish_cases:
        s_a = {k: _mk_attr(k, AttrKind[kind], raw) for k, (kind, raw) in s_map.items()}
        c_a = {k: _mk_attr(k, AttrKind[kind], raw) for k, (kind, raw) in c_map.items()}
        # Token-order-insensitive core_model variation
        rev_tokens = " ".join(reversed(hmodel.split()))
        records.append({
            "pair_id": f"{hid}_hinglish",
            "product_id": hid,
            "category": hcat,
            "slice": "hinglish_transliterated",
            "source_spec": {
                "url": f"https://www.amazon.in/dp/B00{hid}",
                "title": s_title,
                "brand": hbrand,
                "product_type": hptype,
                "core_model": hmodel,
                "gtin": None,
                "mpn": None,
                "attrs": s_a,
                "image_urls": [],
                "listed_price": "450",
            },
            "cand_spec": {
                "url": f"https://www.jiomart.com/p/{hid}",
                "title": c_title,
                "brand": hbrand,
                "product_type": hptype,
                "core_model": rev_tokens,
                "gtin": None,
                "mpn": None,
                "attrs": c_a,
                "image_urls": [],
                "listed_price": "440",
            },
            "expected_bucket": Bucket.EXACT.value,
            "per_attr_truth": {k: Verdict.MATCH.value for k in s_map},
        })

    return records


def write_golden_jsonl(path: Path = GOLDEN_PATH) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = build_golden_records()
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def format_side_by_side(record: dict[str, Any]) -> str:
    src = VariantSpec.model_validate(record["source_spec"])
    cand = VariantSpec.model_validate(record["cand_spec"])
    lines = [
        f"=== Pair: {record['pair_id']} | Slice: {record['slice']} | Expected: {record['expected_bucket']} ===",
        f"  {'FIELD':<16} | {'SOURCE':<38} | {'CANDIDATE':<38}",
        f"  {'-' * 16}-+-{'-' * 38}-+-{'-' * 38}",
        f"  {'brand':<16} | {str(src.brand):<38} | {str(cand.brand):<38}",
        f"  {'core_model':<16} | {src.core_model[:38]:<38} | {cand.core_model[:38]:<38}",
        f"  {'gtin':<16} | {str(src.gtin):<38} | {str(cand.gtin):<38}",
    ]
    all_keys = list(dict.fromkeys(list(src.attrs.keys()) + list(cand.attrs.keys())))
    for k in all_keys:
        sa = src.attrs.get(k)
        ca = cand.attrs.get(k)
        s_str = f"{sa.raw} ({sa.norm})" if sa else "<MISSING>"
        c_str = f"{ca.raw} ({ca.norm})" if ca else "<MISSING>"
        lines.append(f"  {f'attr:{k}':<16} | {s_str[:38]:<38} | {c_str[:38]:<38}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="OneStop Golden Eval Labeling & Inspection CLI")
    parser.add_argument("--build-golden", action="store_true", help="Build golden.jsonl (>=60 products x candidates)")
    parser.add_argument("--inspect", action="store_true", help="Print side-by-side spec view from golden.jsonl")
    parser.add_argument("--limit", type=int, default=5, help="Max pairs to display in --inspect")
    args = parser.parse_args()

    if args.build_golden or not GOLDEN_PATH.exists():
        n = write_golden_jsonl(GOLDEN_PATH)
        print(f"Wrote {n} labeled pairs across 64+ products to {GOLDEN_PATH}")

    if args.inspect:
        with GOLDEN_PATH.open("r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if idx >= args.limit:
                    break
                rec = json.loads(line)
                print(format_side_by_side(rec))
                print()


if __name__ == "__main__":
    main()
